import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import typer
from sqlalchemy import Connection, Engine, text
from sqlmodel import Session

from app.alerts.config import (
    AlertConfig,
    AlertSettings,
    alerts_disabled_reason,
    parse_alert_config,
)
from app.alerts.loop import start_alerts
from app.alerts.schedule import check_rehearsal, polling_window
from app.alerts.service import AlertsRuntime
from app.alerts.status import alert_status, status_line
from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError
from app.core.settings import (
    TweetSettings,
    load_settings,
    parse_league_ids,
)
from app.corroboration.config import CorroborationSettings
from app.corroboration.runtime import build_runtime
from app.corroboration.service import CorroborationRuntime
from app.db.engine import make_engine
from app.db.locks import try_schedule_lock
from app.delivery.channels import build_channel
from app.delivery.channels.base import Channel
from app.delivery.config import DeliverySettings, resolve_delivery
from app.delivery.service import DeliveryService
from app.delivery.store import delivery_summary
from app.extraction.config import load_extraction_settings
from app.extraction.loop import start_extractor
from app.extraction.service import ExtractionRuntime, load_reference_files
from app.extraction.store import extraction_status
from app.fpl.client import FplClient
from app.fpl.deadlines import upcoming_deadlines
from app.llm.chat import PROVIDER, build_chat_model, resolve_llm
from app.llm.settings import load_llm_settings
from app.llm.tracing import resolve_tracing
from app.retrieval.config import load_retrieval_settings, resolve_embedding
from app.retrieval.embedder import build_embedder
from app.retrieval.indexing import IndexingRuntime
from app.retrieval.loop import start_indexer
from app.tweets.config import resolve_ingest
from app.tweets.loop import start_poller
from app.tweets.schedule import WINDOW, mode, next_poll_at
from app.tweets.sources import build_source
from app.tweets.sources.base import TweetSource
from app.tweets.store import latest_poll, latest_success_by_source
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
class AlertsSetup:
    config: AlertConfig
    make_channel: Callable[[], Channel]
    make_corroboration: Callable[[], CorroborationRuntime]
    clock: Clock | None = None


@dataclass(frozen=True)
class WorkerDeps:
    engine: Engine
    client: FplClient
    league_ids_raw: str
    clock: Clock
    tweet_ingest: TweetIngest | None = None
    extraction: ExtractionRuntime | None = None
    indexing: IndexingRuntime | None = None
    delivery_channel: str | None = None
    alerts: AlertsSetup | None = None
    alerts_disabled_reason: str | None = None


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
        retrieval_settings = load_retrieval_settings()
        embedding_config = resolve_embedding(retrieval_settings)
        delivery_config = resolve_delivery(DeliverySettings())
        alert_settings = AlertSettings()
        alert_config = parse_alert_config(alert_settings)
        disabled_reason = alerts_disabled_reason(
            alert_settings,
            delivery=delivery_config is not None,
            tweet_ingest=ingest_config is not None,
            extraction=llm_config is not None,
        )
        corroboration_settings = (
            load_llm_settings(CorroborationSettings) if disabled_reason is None else None
        )
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
    indexing = None
    if embedding_config is not None:
        indexing = IndexingRuntime(
            model=embedding_config.model,
            make_embedder=lambda: build_embedder(embedding_config),
            prices=embedding_config.prices,
            tracing=resolve_tracing(retrieval_settings),
        )
    alerts = None
    if disabled_reason is None:
        assert delivery_config is not None and corroboration_settings is not None
        alerts = AlertsSetup(
            config=alert_config,
            make_channel=lambda: build_channel(delivery_config),
            make_corroboration=lambda: build_runtime(corroboration_settings, SystemClock()),
        )
    return WorkerDeps(
        engine=make_engine(settings.database_url),
        client=FplClient(),
        league_ids_raw=settings.fpl_league_ids,
        clock=SystemClock(),
        tweet_ingest=tweet_ingest,
        extraction=extraction,
        indexing=indexing,
        delivery_channel=delivery_config.provider if delivery_config else None,
        alerts=alerts,
        alerts_disabled_reason=disabled_reason,
    )


def _alerts_runtime(
    deps: WorkerDeps, setup: AlertsSetup, league_ids: list[int], stop_event: threading.Event
) -> tuple[AlertsRuntime, Channel]:
    now = deps.clock.now()
    real_deadlines = [g.deadline_at for g in load_state(deps.engine).gameweeks]
    check_rehearsal(setup.config, real_deadlines, now)
    corroboration = setup.make_corroboration()
    channel = setup.make_channel()
    delivery = DeliveryService(deps.engine, channel, setup.clock or SystemClock(), stop_event)
    return AlertsRuntime(setup.config, league_ids, delivery, corroboration), channel


def _polling(deps: WorkerDeps) -> tuple[timedelta, tuple[datetime, ...]]:
    if deps.alerts is None:
        return WINDOW, ()
    rehearsal = deps.alerts.config.rehearsal_deadline
    return polling_window(deps.alerts.config), (rehearsal,) if rehearsal is not None else ()


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
    alerts_runtime: AlertsRuntime | None = None
    alerts_channel: Channel | None = None
    if deps.alerts is not None:
        try:
            alerts_runtime, alerts_channel = _alerts_runtime(
                deps, deps.alerts, league_ids, stop_event
            )
        except CollectorError as exc:
            raise fail(str(exc)) from None

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
    alerts_thread: threading.Thread | None = None
    extraction_thread: threading.Thread | None = None
    indexing_thread: threading.Thread | None = None
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
            window, extra_deadlines = _polling(deps)
            tweet_thread = start_poller(
                deps.engine,
                deps.tweet_ingest.make_source,
                deps.tweet_ingest.list_id,
                stop_event,
                clock=deps.tweet_ingest.clock,
                window=window,
                extra_deadlines=extra_deadlines,
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

        if deps.indexing is None:
            logger.info("retrieval indexing disabled")
        else:
            indexing_thread = start_indexer(
                deps.engine, deps.indexing, stop_event, clock=deps.indexing.clock
            )
            logger.info("retrieval indexing started: model=%s", deps.indexing.model)

        if alerts_runtime is None:
            logger.info("alerts disabled: %s", deps.alerts_disabled_reason)
        else:
            assert deps.alerts is not None
            alerts_thread = start_alerts(
                deps.engine, alerts_runtime, stop_event, clock=deps.alerts.clock
            )
            logger.info("alerts started: slots=%s", ",".join(map(str, deps.alerts.config.slots)))

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
        threads = [tweet_thread, extraction_thread, indexing_thread, alerts_thread]
        if any(thread is not None for thread in threads):
            stop_event.set()
            deadline = time.monotonic() + 5
            for thread in threads:
                if thread is not None:
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if alerts_channel is not None:
            alerts_channel.close()
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
        window, extra_deadlines = _polling(deps)
        with Session(deps.engine) as session:
            deadlines = sorted([*upcoming_deadlines(session, now), *extra_deadlines])
        last_success = latest_success_by_source(deps.engine).get(source_name)
        last = latest_poll(deps.engine, source_name)
        next_at = next_poll_at(deadlines, last, now, window)
        typer.echo("Tweet ingest:")
        typer.echo(f"  source: {source_name}")
        last_success_str = "never" if last_success is None else _fmt(last_success.started_at)
        typer.echo(f"  last successful poll: {last_success_str}")
        next_str = "due now" if next_at <= now else _fmt(next_at)
        typer.echo(f"  next poll: {next_str}")
        typer.echo(f"  mode: {mode(deadlines, now, window)}")

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

    if deps.delivery_channel is None:
        typer.echo("Delivery: disabled")
    else:
        summary = delivery_summary(deps.engine, now)
        if summary.last_sent_at is None:
            last_sent = "never"
        else:
            last_sent = f"{_fmt(summary.last_sent_at)} ({summary.last_sent_kind})"
        typer.echo(
            f"Delivery: {deps.delivery_channel}  last sent: {last_sent}"
            f"  failed in 24 h: {summary.failed_last_24h}"
        )

    if deps.alerts is None:
        typer.echo(f"Alerts: disabled ({deps.alerts_disabled_reason or 'not configured'})")
    else:
        typer.echo(status_line(alert_status(deps.engine, deps.alerts.config, now), _fmt))


def main() -> None:
    app(prog_name="python -m app.worker")
