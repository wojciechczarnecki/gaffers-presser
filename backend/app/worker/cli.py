import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import typer
from sqlalchemy import Connection, Engine, text
from sqlmodel import Session

from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError
from app.core.settings import (
    TweetSettings,
    load_extraction_settings,
    load_settings,
    parse_league_ids,
)
from app.db.engine import make_engine
from app.db.locks import try_schedule_lock
from app.extraction.config import PROVIDER, resolve_llm, resolve_tracing
from app.extraction.loop import start_extractor
from app.extraction.providers import build_chat_model
from app.extraction.service import ExtractionRuntime, load_reference_files
from app.extraction.store import extraction_status
from app.fpl.client import FplClient
from app.tweets.config import resolve_ingest
from app.tweets.loop import start_poller
from app.tweets.schedule import mode, next_poll_at
from app.tweets.sources import build_source
from app.tweets.sources.base import TweetSource
from app.tweets.store import latest_poll, latest_success_by_source, upcoming_deadlines
from app.worker.jobs import Shutdown
from app.worker.loop import Worker
from app.worker.schedule import Job, outlook
from app.worker.store import latest_runs_by_job, load_state

logger = logging.getLogger(__name__)

SCHEDULE_LOCK_POLL_SECONDS = 30.0
WORKER_LOG_HANDLER = "app.worker"


@dataclass(frozen=True)
class TweetIngest:
    source_name: str
    list_id: int
    make_source: Callable[[], TweetSource]
    clock: Clock | None = None


@dataclass(frozen=True)
class WorkerDeps:
    engine: Engine
    client: FplClient
    league_ids_raw: str
    clock: Clock
    tweet_ingest: TweetIngest | None = None
    extraction: ExtractionRuntime | None = None


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="The deadline-driven FPL worker.",
)


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(h.get_name() == WORKER_LOG_HANDLER for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(WORKER_LOG_HANDLER)
    handler.setFormatter(
        _UtcFormatter(
            fmt="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(handler)


def get_deps(ctx: typer.Context) -> WorkerDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _deps_from_settings() -> WorkerDeps:
    _configure_logging()
    try:
        tweet_settings = TweetSettings()
        ingest_config = resolve_ingest(tweet_settings)
        extraction_settings = load_extraction_settings()
        llm_config = resolve_llm(extraction_settings)
        prices, aliases = load_reference_files() if llm_config is not None else ({}, ([], []))
        settings = load_settings()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    tweet_ingest = None
    if ingest_config is not None:
        tweet_ingest = TweetIngest(
            source_name=ingest_config.source_name,
            list_id=ingest_config.list_id,
            make_source=lambda: build_source(ingest_config.source_name, ingest_config.settings),
        )
    extraction = None
    if llm_config is not None:
        extraction = ExtractionRuntime(
            provider=PROVIDER,
            model=llm_config.model,
            fallback_model=llm_config.fallback_model,
            make_spec=lambda: build_chat_model(llm_config),
            tracing=resolve_tracing(extraction_settings),
            prices=prices,
            aliases=aliases,
        )
    return WorkerDeps(
        engine=make_engine(settings.database_url),
        client=FplClient(),
        league_ids_raw=settings.fpl_league_ids,
        clock=SystemClock(),
        tweet_ingest=tweet_ingest,
        extraction=extraction,
    )


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.command(help="Run the deadline-driven worker until stopped.")
def run(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    try:
        league_ids = parse_league_ids(deps.league_ids_raw)
    except CollectorError as exc:
        raise fail(str(exc)) from None

    stop_event = threading.Event()
    signalled = threading.Event()

    def handle_signal(signum: int, frame: object) -> None:
        stop_event.set()
        if signalled.is_set():
            return
        signalled.set()
        raise Shutdown

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    lock_connection: Connection | None = None
    tweet_thread: threading.Thread | None = None
    extraction_thread: threading.Thread | None = None
    try:
        lock_connection = deps.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        logged_waiting = False
        while not try_schedule_lock(lock_connection):
            if not logged_waiting:
                logger.info("waiting for the schedule lock held by another worker")
                logged_waiting = True
            deps.clock.sleep(SCHEDULE_LOCK_POLL_SECONDS)

        if deps.tweet_ingest is None:
            logger.info("tweet ingest disabled")
        else:
            tweet_thread = start_poller(
                deps.engine,
                deps.tweet_ingest.make_source,
                deps.tweet_ingest.list_id,
                stop_event,
                clock=deps.tweet_ingest.clock,
            )

        if deps.extraction is None:
            logger.info("extraction disabled")
        else:
            if deps.extraction.tracing is None:
                logger.warning(
                    "langfuse tracing disabled: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set"
                )
            extraction_thread = start_extractor(
                deps.engine, deps.extraction, stop_event, clock=deps.extraction.clock
            )

        def heartbeat() -> None:
            lock_connection.execute(text("SELECT 1"))

        worker = Worker(
            deps.engine,
            deps.client,
            league_ids,
            deps.clock,
            stop_event=stop_event,
            heartbeat=heartbeat,
        )
        worker.run()
    except Shutdown:
        logger.info("worker stopped")
    except Exception as exc:
        logger.error("worker failed: %s", type(exc).__name__)
        raise typer.Exit(1) from None
    finally:
        if tweet_thread is not None or extraction_thread is not None:
            stop_event.set()
            deadline = time.monotonic() + 5
            if tweet_thread is not None:
                tweet_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if extraction_thread is not None:
                extraction_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        if lock_connection is not None:
            # A pooled connection returned via close() keeps its session-held advisory
            # lock until the pool actually disconnects it; invalidate() forces that now.
            lock_connection.invalidate()
            lock_connection.close()


@app.command(help="Print the latest run of each job and the next planned actions.")
def status(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    now = deps.clock.now()

    latest = latest_runs_by_job(deps.engine)
    typer.echo("Latest runs:")
    for job in Job:
        row = latest[job]
        if row is None:
            typer.echo(f"  {job.value}: never")
            continue
        gw = row.gameweek_fpl_id if row.gameweek_fpl_id is not None else "-"
        typer.echo(f"  {job.value}: {_fmt(row.started_at)} gameweek={gw} outcome={row.outcome}")

    typer.echo("Next actions:")
    state = load_state(deps.engine)
    for action in outlook(state, now):
        gw = action.gameweek if action.gameweek is not None else "-"
        when = "due now" if action.at <= now else _fmt(action.at)
        typer.echo(f"  {action.job.value} gameweek={gw}: {when}")

    if deps.tweet_ingest is None:
        typer.echo("Tweet ingest: disabled")
    else:
        source_name = deps.tweet_ingest.source_name
        with Session(deps.engine) as session:
            deadlines = upcoming_deadlines(session, now)
        last_success = latest_success_by_source(deps.engine).get(source_name)
        last = latest_poll(deps.engine, source_name)
        next_at = next_poll_at(deadlines, last, now)
        typer.echo("Tweet ingest:")
        typer.echo(f"  source: {source_name}")
        last_success_str = "never" if last_success is None else _fmt(last_success.started_at)
        typer.echo(f"  last successful poll: {last_success_str}")
        next_str = "due now" if next_at <= now else _fmt(next_at)
        typer.echo(f"  next poll: {next_str}")
        typer.echo(f"  mode: {mode(deadlines, now)}")

    if deps.extraction is None:
        typer.echo("Extraction: disabled")
    else:
        extraction_state = extraction_status(deps.engine)
        typer.echo("Extraction:")
        typer.echo(f"  model: {deps.extraction.provider}:{deps.extraction.model}")
        typer.echo(f"  fallback: {deps.extraction.fallback_model or 'none'}")
        typer.echo(f"  posts waiting: {extraction_state.waiting}")
        typer.echo(f"  failed posts: {extraction_state.failed_posts}")
        if extraction_state.latest is None:
            typer.echo("  latest extraction: never")
        else:
            latest_extraction = extraction_state.latest
            latency_str = (
                "-"
                if latest_extraction.latency_seconds is None
                else str(latest_extraction.latency_seconds)
            )
            typer.echo(
                f"  latest extraction: {_fmt(latest_extraction.finished_at)}"
                f" x_id={latest_extraction.tweet_x_id} status={latest_extraction.status}"
                f" latency={latency_str}"
            )


def main() -> None:
    app(prog_name="python -m app.worker")
