import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, func, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, select

from app.core.clock import Clock
from app.core.errors import AppError
from app.corroboration.evaluation.dataset import BuildWindow
from app.extraction.fixtures import FixtureRecord
from app.extraction.linking import PlayerRecord, TeamRecord
from app.extraction.models import Extraction
from app.extraction.service import ExtractionRuntime, extract_post
from app.extraction.store import _post_from_tweet
from app.extraction.tracing import NULL_EXTRACTION_TRACER
from app.fpl.calendar import current_season
from app.fpl.models.reference import Fixture, Gameweek, Player, Season, Team
from app.retrieval.embedder import Embedder
from app.retrieval.evaluation.dataset import CorpusPost
from app.retrieval.evaluation.players import gameweek_window
from app.retrieval.evaluation.schema import CORPUS_SOURCE, check_source_flags
from app.retrieval.indexing import IndexingRuntime, index_missing
from app.retrieval.models import PostEmbedding
from app.retrieval.tracing import NULL_TRACER
from app.tweets.classes import source_post_sql
from app.tweets.models import ListMembership, Tweet

logger = logging.getLogger(__name__)

COMPOSE_PORT = 5432
# FPL's transfer deadline is 90 minutes before the first kickoff of the gameweek. Checked against
# retrieval set v2's recorded GW6 and GW7 deadlines (spec 019).
DEADLINE_LEAD = timedelta(minutes=90)


class ScratchError(AppError):
    pass


def guard_scratch_url(url: str, configured_url: str | None) -> None:
    parsed = make_url(url)
    port = parsed.port or COMPOSE_PORT
    if port == COMPOSE_PORT:
        raise ScratchError("the scratch database must not use port 5432, the Compose port")
    if configured_url:
        configured = make_url(configured_url)
        same = (parsed.host, port, parsed.database) == (
            configured.host,
            configured.port or COMPOSE_PORT,
            configured.database,
        )
        if same:
            raise ScratchError("the scratch database is the configured DATABASE_URL")


def derive_deadlines(fixtures: Sequence[FixtureRecord]) -> dict[int, datetime]:
    first: dict[int, datetime] = {}
    for fixture in fixtures:
        if fixture.gameweek is None or fixture.kickoff_at is None:
            continue
        if fixture.gameweek not in first or fixture.kickoff_at < first[fixture.gameweek]:
            first[fixture.gameweek] = fixture.kickoff_at
    return {gameweek: kickoff - DEADLINE_LEAD for gameweek, kickoff in first.items()}


def seed_scratch(
    engine: Engine,
    corpus: Sequence[CorpusPost],
    list_handles: Sequence[str],
    players: Sequence[PlayerRecord],
    teams: Sequence[TeamRecord],
    fixtures: Sequence[FixtureRecord],
) -> bool:
    with Session(engine) as session:
        sources = set(session.exec(select(Tweet.source).distinct()).all())
    if sources and sources != {CORPUS_SOURCE}:
        raise ScratchError("the database holds other posts than the evaluation corpus")
    if sources:
        return False

    season = players[0].season if players else teams[0].season
    deadlines = derive_deadlines(fixtures)
    corpus_end = max((post.created_at for post in corpus), default=None)
    with Session(engine) as session, session.begin():
        session.add(Season(label=season))
        session.flush()
        session.add_all(
            Team(season=season, fpl_id=t.fpl_id, name=t.name, short_name=t.short_name)
            for t in teams
        )
        session.flush()
        session.add_all(
            Player(
                season=season,
                fpl_id=p.fpl_id,
                web_name=p.web_name,
                first_name=p.first_name,
                second_name=p.second_name,
                team_fpl_id=p.team_fpl_id,
                position=0,
            )
            for p in players
        )
        session.add_all(
            Gameweek(
                season=season,
                fpl_id=number,
                name=f"Gameweek {number}",
                deadline_at=deadline,
                finished=corpus_end is not None and deadline < corpus_end,
                data_checked=False,
            )
            for number, deadline in deadlines.items()
        )
        session.flush()
        session.add_all(
            Fixture(
                season=season,
                fpl_id=f.fpl_id,
                gameweek_fpl_id=f.gameweek if f.gameweek in deadlines else None,
                kickoff_at=f.kickoff_at,
                team_h_fpl_id=f.team_h_fpl_id,
                team_a_fpl_id=f.team_a_fpl_id,
                finished=corpus_end is not None
                and f.kickoff_at is not None
                and f.kickoff_at < corpus_end,
            )
            for f in fixtures
        )
        session.add(
            ListMembership(
                list_id=0,
                source=CORPUS_SOURCE,
                fetched_at=corpus_end or datetime.now().astimezone(),
                handles=list(list_handles),
            )
        )
        session.flush()
        session.execute(
            Tweet.__table__.insert(),
            [
                {
                    "x_id": post.x_id,
                    "author_handle": post.author_handle,
                    "text": post.text,
                    "created_at": post.created_at,
                    "first_fetched_at": post.created_at,
                    "source": CORPUS_SOURCE,
                    "is_repost": post.is_repost,
                    "is_reply": post.is_reply,
                    "quoted_x_id": post.quoted_x_id,
                    "raw": {},
                }
                for post in corpus
            ],
        )
    check_source_flags(engine, corpus)
    return True


def window_for(session: Session, gameweek: int) -> BuildWindow:
    season = current_season(session)
    if season is None:
        raise ScratchError("the database has no season")
    window = gameweek_window(session, season, gameweek, "alerts")
    return BuildWindow(gameweek=gameweek, start=window.window_start, end=window.window_end)


@dataclass(frozen=True)
class ExtractSummary:
    extracted: int
    failed: int
    stopped_by_cap: bool


def _extraction_cost(engine: Engine) -> float:
    with Session(engine) as session:
        return float(session.exec(select(func.coalesce(func.sum(Extraction.cost_usd), 0.0))).one())


def extract_window(
    engine: Engine,
    runtime: ExtractionRuntime,
    since: datetime,
    until: datetime,
    max_cost_usd: float,
    clock: Clock,
    stop: threading.Event,
) -> ExtractSummary:
    with Session(engine) as session:
        done = select(Extraction.tweet_x_id).where(Extraction.status == "extracted")
        tweets = session.exec(
            select(Tweet)
            .where(Tweet.created_at >= since, Tweet.created_at < until)
            .where(text(source_post_sql("tweet")))
            .where(Tweet.x_id.not_in(done))
            .order_by(Tweet.created_at.desc(), Tweet.x_id.desc())
        ).all()
        posts = [_post_from_tweet(tweet) for tweet in tweets]
    extracted = failed = 0
    for post in posts:
        if stop.is_set():
            break
        if _extraction_cost(engine) >= max_cost_usd:
            return ExtractSummary(extracted, failed, True)
        outcome = extract_post(
            engine, runtime, post, clock, stop, NULL_EXTRACTION_TRACER, record_latency=False
        )
        if outcome is None:
            break
        if outcome.status == "extracted":
            extracted += 1
        else:
            failed += 1
    return ExtractSummary(extracted, failed, False)


def build_costs(engine: Engine) -> tuple[float, float]:
    with Session(engine) as session:
        embeddings = session.exec(
            select(func.coalesce(func.sum(PostEmbedding.cost_usd), 0.0))
        ).one()
    return float(embeddings), _extraction_cost(engine)


@dataclass(frozen=True)
class ScratchSummary:
    seeded: bool
    embedded: int
    windows: list[BuildWindow]
    extract: ExtractSummary
    embeddings_cost_usd: float
    extraction_cost_usd: float


def run_build_scratch(
    engine: Engine,
    corpus: Sequence[CorpusPost],
    list_handles: Sequence[str],
    players: Sequence[PlayerRecord],
    teams: Sequence[TeamRecord],
    fixtures: Sequence[FixtureRecord],
    *,
    embedder: Embedder,
    indexing: IndexingRuntime,
    extraction: ExtractionRuntime,
    gameweeks: Sequence[int],
    max_cost_usd: float,
    clock: Clock,
    stop: threading.Event,
) -> ScratchSummary:
    seeded = seed_scratch(engine, corpus, list_handles, players, teams, fixtures)
    # Untraced, like the retrieval evaluation's corpus indexing.
    indexed = index_missing(engine, indexing, embedder, NULL_TRACER, clock, stop)
    if indexed.failed:
        raise ScratchError(f"{indexed.failed} posts could not be embedded")
    with Session(engine) as session:
        windows = [window_for(session, gameweek) for gameweek in gameweeks]
    extract = extract_window(
        engine,
        extraction,
        min(w.start for w in windows),
        max(w.end for w in windows),
        max_cost_usd,
        clock,
        stop,
    )
    embeddings, extraction_cost = build_costs(engine)
    return ScratchSummary(seeded, indexed.embedded, windows, extract, embeddings, extraction_cost)
