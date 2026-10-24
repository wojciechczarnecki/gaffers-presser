from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from app.corroboration.schemas import Claim, PlayerRef, PostRef
from app.extraction.linking import (
    PlayerAlias,
    PlayerIndex,
    TeamAlias,
    load_aliases,
    load_players,
)
from app.extraction.store import current_extractions
from app.fpl.deadlines import deadline_at_or_before
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.retrieval.search import Mode, SearchFilters, search
from app.retrieval.tracing import NULL_TRACER, RetrievalTracer
from app.retrieval.variants import Variant, load_player_names, variant_text
from app.tweets.classes import quoted_authors

SEARCH_LIMIT = 20
MAX_JUDGED = 10
EMBED_TIMEOUT_SECONDS = 5.0
DEFAULT_LOOKBACK = timedelta(days=7)
QUERY_VARIANT: Variant = "names"
SEARCH_MODE: Mode = "hybrid"

Aliases = tuple[list[PlayerAlias], list[TeamAlias]]


@dataclass(frozen=True)
class Candidates:
    posts: list[PostRef]
    failed_legs: tuple[str, ...] = ()
    failure: str | None = None


def _post_ref(
    x_id: int,
    author_handle: str,
    reposted_author_handle: str | None,
    is_repost: bool,
    created_at: datetime,
    text: str,
    quoted_author_handle: str | None = None,
) -> PostRef:
    return PostRef(
        x_id=x_id,
        author_handle=author_handle,
        reposted_author_handle=reposted_author_handle,
        is_repost=is_repost,
        created_at=created_at,
        text=text,
        quoted_author_handle=quoted_author_handle,
    )


def resolve_player(session: Session, text: str, aliases: Aliases | None = None) -> list[PlayerRef]:
    players, teams = load_players(session)
    player_aliases, team_aliases = aliases if aliases is not None else load_aliases()
    index = PlayerIndex(players, teams, player_aliases, team_aliases)
    stripped = text.strip()
    if stripped.isdigit():
        found = [player for player in players if player.fpl_id == int(stripped)]
    else:
        found = index.resolve(stripped, None)
    refs = []
    for player in found:
        team = index.team_for(player.team_fpl_id)
        refs.append(
            PlayerRef(
                season=player.season,
                fpl_id=player.fpl_id,
                web_name=player.web_name,
                team_name=team.name if team is not None else None,
            )
        )
    return refs


def window_start(session: Session, as_of: datetime, since: datetime | None = None) -> datetime:
    if since is not None:
        return since
    deadline = deadline_at_or_before(session, as_of)
    return deadline if deadline is not None else as_of - DEFAULT_LOOKBACK


def sql_claims(
    session: Session, player: PlayerRef, start: datetime, as_of: datetime
) -> list[Claim]:
    claims = []
    rows = current_extractions(
        session,
        created_from=start,
        created_until=as_of,
        player=(player.season, player.fpl_id),
        sources_only=True,
        fresh_lineups_only=True,
    )
    quoted = quoted_authors(session, [row.tweet_x_id for row in rows])
    for row in rows:
        event = next(
            (
                e
                for e in row.events
                if e.player_season == player.season and e.player_fpl_id == player.fpl_id
            ),
            None,
        )
        if event is None:
            continue
        claims.append(
            Claim(
                post=_post_ref(
                    row.tweet_x_id,
                    row.author_handle,
                    row.reposted_author_handle,
                    row.is_repost,
                    row.created_at,
                    row.text,
                    quoted.get(row.tweet_x_id),
                ),
                event_type=event.event_type,
                certainty=event.certainty,
            )
        )
    return claims


def claimed_post_ids(
    session: Session, player: PlayerRef, start: datetime, as_of: datetime
) -> set[int]:
    # Unfiltered: a post whose only event about the player is a stale line-up must not come back
    # through retrieval either.
    rows = current_extractions(
        session,
        created_from=start,
        created_until=as_of,
        player=(player.season, player.fpl_id),
        sources_only=True,
    )
    return {row.tweet_x_id for row in rows}


def _query_text(
    engine: Engine, player: PlayerRef, variant: Variant, aliases: list[PlayerAlias] | None
) -> str:
    if variant == "web_name":
        return player.web_name
    with Session(engine) as session:
        names = load_player_names(session, player.season, player.fpl_id, aliases)
    return variant_text(names, variant) if names is not None else player.web_name


def retrieval_candidates(
    engine: Engine,
    player: PlayerRef,
    start: datetime,
    as_of: datetime,
    *,
    embedder: Embedder,
    claimed_ids: Collection[int] = (),
    tracer: RetrievalTracer = NULL_TRACER,
    prices: dict[str, Price] | None = None,
    embed_timeout_seconds: float = EMBED_TIMEOUT_SECONDS,
    variant: Variant | None = None,
    aliases: list[PlayerAlias] | None = None,
) -> Candidates:
    response = search(
        engine,
        _query_text(engine, player, variant or QUERY_VARIANT, aliases),
        SEARCH_MODE,
        embedder=embedder,
        filters=SearchFilters(since=start, until=as_of, sources_only=True),
        limit=SEARCH_LIMIT,
        tracer=tracer,
        prices=prices,
        embed_timeout_seconds=embed_timeout_seconds,
    )
    results = [r for r in response.results if r.x_id not in claimed_ids]
    with Session(engine) as session:
        quoted = quoted_authors(session, [r.x_id for r in results])
    posts = [
        _post_ref(
            r.x_id,
            r.author_handle,
            r.reposted_author_handle,
            r.is_repost,
            r.created_at,
            r.text,
            quoted.get(r.x_id),
        )
        for r in results
    ]
    return Candidates(
        posts=posts[:MAX_JUDGED], failed_legs=response.failed_legs, failure=response.failure
    )
