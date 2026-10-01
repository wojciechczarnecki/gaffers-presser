from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from app.extraction.schemas import LinkedEvent
from app.extraction.store import ExtractionRecord, save_extraction
from app.fpl.models.reference import Gameweek, Player, Season, Team
from tests.retrieval.helpers import add_tweet

SEASON = "2026/27"
NOW = datetime(2026, 9, 29, 18, 0, tzinfo=UTC)

SAKA = 1
ISAK = 2
GABRIEL_JESUS = 3
GABRIEL_MAGALHAES = 4
PLAYERS = [
    (SAKA, "Saka", "Bukayo", "Saka", 1),
    (ISAK, "Isak", "Alexander", "Isak", 2),
    (GABRIEL_JESUS, "Jesus", "Gabriel", "Jesus", 1),
    (GABRIEL_MAGALHAES, "Gabriel", "Gabriel", "dos Santos Magalhaes", 1),
]


def seed_reference(engine: Engine, deadlines: dict[int, datetime] | None = None) -> None:
    with Session(engine) as session, session.begin():
        session.add(Season(label=SEASON))
        session.flush()
        session.add(Team(season=SEASON, fpl_id=1, name="Arsenal", short_name="ARS"))
        session.add(Team(season=SEASON, fpl_id=2, name="Newcastle", short_name="NEW"))
        session.flush()
        for fpl_id, web_name, first, second, team in PLAYERS:
            session.add(
                Player(
                    season=SEASON,
                    fpl_id=fpl_id,
                    web_name=web_name,
                    first_name=first,
                    second_name=second,
                    team_fpl_id=team,
                    position=3,
                )
            )
        for fpl_id, deadline in (deadlines or {}).items():
            session.add(
                Gameweek(
                    season=SEASON,
                    fpl_id=fpl_id,
                    name=f"Gameweek {fpl_id}",
                    deadline_at=deadline,
                    finished=True,
                    data_checked=True,
                )
            )


def add_claim(
    engine: Engine,
    x_id: int,
    player_fpl_id: int | None,
    event_type: str = "doubt",
    certainty: str = "likely",
    created_at: datetime | None = None,
    author: str | None = None,
    text: str | None = None,
    is_repost: bool = False,
    reposted_author_handle: str | None = None,
    finished: int = 0,
    mention: str = "Saka",
) -> int:
    created_at = created_at or NOW - timedelta(hours=1)
    add_tweet(
        engine,
        x_id,
        text=text or f"post {x_id} about {mention}",
        created_at=created_at,
        is_repost=is_repost,
        author=author or f"author{x_id}",
        reposted_author_handle=reposted_author_handle,
    )
    return add_extraction(engine, x_id, player_fpl_id, event_type, certainty, finished, mention)


def add_extraction(
    engine: Engine,
    x_id: int,
    player_fpl_id: int | None,
    event_type: str = "doubt",
    certainty: str = "likely",
    finished: int = 0,
    mention: str = "Saka",
    status: str = "extracted",
) -> int:
    events = []
    if player_fpl_id is not None:
        events.append(
            LinkedEvent(
                mention=mention,
                team=None,
                player_season=SEASON,
                player_fpl_id=player_fpl_id,
                event_type=event_type,
                certainty=certainty,
            )
        )
    with Session(engine) as session:
        return save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=x_id,
                status=status,
                provider="fake",
                model="fake-model",
                prompt_version="extraction@1+link_disambiguation@1",
                started_at=NOW,
                finished_at=NOW + timedelta(seconds=finished),
                attempts=1,
            ),
            events,
        )
