import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.extraction.linking import load_aliases
from app.extraction.store import current_extractions
from app.fpl.deadlines import DEFAULT_MAX_LOOKBACK, window_floor
from app.fpl.models.reference import Fixture, Gameweek, Player, Team
from app.retrieval.evaluation.dataset import (
    ClaimedPost,
    EvaluationError,
    PlayerInfo,
    PlayerQuery,
    WindowRule,
)
from app.retrieval.evaluation.labels import AnyQuery
from app.retrieval.evaluation.queries import DEV_SHARE
from app.retrieval.variants import load_player_names

MATCH_LENGTH = timedelta(hours=2)


@dataclass(frozen=True)
class GameweekWindow:
    season: str
    gameweek: int
    deadline_at: datetime
    window_start: datetime
    window_end: datetime
    rule: WindowRule
    previous_last_match_end: datetime | None


def gameweek_window(
    session: Session, season: str, gameweek: int, rule: WindowRule
) -> GameweekWindow:
    deadline = session.exec(
        select(Gameweek.deadline_at).where(Gameweek.season == season, Gameweek.fpl_id == gameweek)
    ).first()
    if deadline is None:
        raise EvaluationError(f"gameweek {gameweek} of {season} is not in the database")
    previous_deadline = session.exec(
        select(func.max(Gameweek.deadline_at)).where(
            Gameweek.season == season, Gameweek.deadline_at < deadline
        )
    ).one()
    last_kickoff = session.exec(
        select(func.max(Fixture.kickoff_at)).where(
            Fixture.season == season, Fixture.gameweek_fpl_id == gameweek - 1
        )
    ).one()
    previous_end = last_kickoff + MATCH_LENGTH if last_kickoff is not None else None
    if rule == "after-last-match":
        if previous_end is None:
            raise EvaluationError(
                f"gameweek {gameweek - 1} has no fixture with a kickoff time; use --window alerts"
            )
        start = previous_end
    else:
        start = window_floor(previous_deadline, deadline, DEFAULT_MAX_LOOKBACK, deadline)
    return GameweekWindow(season, gameweek, deadline, start, deadline, rule, previous_end)


def build_player_slice(
    engine: Engine, season: str, gameweek: int, rule: WindowRule, seed: int
) -> list[PlayerQuery]:
    aliases = load_aliases()[0]
    with Session(engine) as session:
        window = gameweek_window(session, season, gameweek, rule)
        rows = current_extractions(
            session,
            created_from=window.window_start,
            created_until=window.window_end,
            sources_only=True,
        )
        claims: dict[int, dict[int, datetime]] = {}
        for row in rows:
            for event in row.events:
                if event.player_fpl_id is None or event.player_season != season:
                    continue
                claims.setdefault(event.player_fpl_id, {})[row.tweet_x_id] = row.created_at

        stale_before = window.previous_last_match_end
        queries = []
        for fpl_id in sorted(claims):
            names = load_player_names(session, season, fpl_id, aliases)
            player = session.get(Player, (season, fpl_id))
            if names is None or player is None:
                continue
            team = session.get(Team, (season, player.team_fpl_id))
            claimed = [
                ClaimedPost(
                    x_id=x_id,
                    created_at=created_at,
                    stale=stale_before is not None and created_at < stale_before,
                )
                for x_id, created_at in sorted(claims[fpl_id].items(), key=lambda i: (i[1], i[0]))
            ]
            queries.append(
                PlayerQuery(
                    id=f"p-gw{gameweek}-{fpl_id}",
                    split="test",
                    player=PlayerInfo(
                        season=season,
                        fpl_id=fpl_id,
                        web_name=names.web_name,
                        first_name=names.first_name,
                        second_name=names.second_name,
                        team=team.name if team is not None else "",
                        aliases=list(names.aliases),
                    ),
                    gameweek=gameweek,
                    deadline_at=window.deadline_at,
                    window_start=window.window_start,
                    window_end=window.window_end,
                    window_rule=rule,
                    previous_last_match_end=window.previous_last_match_end,
                    claimed=claimed,
                    judgements=[],
                )
            )
    return assign_splits(queries, seed)


def merge_player_queries(
    existing: Sequence[AnyQuery], built: Sequence[PlayerQuery], gameweek: int
) -> list[AnyQuery]:
    # A set holds several gameweeks: the built gameweek replaces only its own queries, and
    # each gameweek keeps the dev/test split its build drew.
    players = [q for q in existing if isinstance(q, PlayerQuery) and q.gameweek != gameweek]
    others = [q for q in existing if not isinstance(q, PlayerQuery)]
    return [*players, *built, *others]


def assign_splits(queries: list[PlayerQuery], seed: int) -> list[PlayerQuery]:
    order = list(range(len(queries)))
    random.Random(seed).shuffle(order)
    dev = {index for index in order[: round(DEV_SHARE * len(queries))]}
    return [
        query.model_copy(update={"split": "dev" if index in dev else "test"})
        for index, query in enumerate(queries)
    ]
