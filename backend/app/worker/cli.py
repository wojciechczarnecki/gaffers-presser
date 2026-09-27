import logging
import signal
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import typer
from sqlalchemy import Engine, text

from app.core.errors import CollectorError
from app.core.settings import load_settings, parse_league_ids
from app.db.engine import make_engine
from app.db.locks import try_schedule_lock
from app.fpl.client import FplClient
from app.worker.jobs import Shutdown
from app.worker.loop import Clock, SystemClock, Worker
from app.worker.schedule import Job, plan
from app.worker.store import latest_runs_by_job, load_state

logger = logging.getLogger(__name__)

SCHEDULE_LOCK_POLL_SECONDS = 30.0


@dataclass(frozen=True)
class WorkerDeps:
    engine: Engine
    client: FplClient
    league_ids_raw: str
    clock: Clock


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
    handler = logging.StreamHandler()
    handler.setFormatter(
        _UtcFormatter(
            fmt="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def get_deps(ctx: typer.Context) -> WorkerDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _deps_from_settings() -> WorkerDeps:
    _configure_logging()
    try:
        settings = load_settings()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    return WorkerDeps(
        engine=make_engine(settings.database_url),
        client=FplClient(),
        league_ids_raw=settings.fpl_league_ids,
        clock=SystemClock(),
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

    lock_connection = deps.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        logged_waiting = False
        while not try_schedule_lock(lock_connection):
            if not logged_waiting:
                logger.info("waiting for the schedule lock held by another worker")
                logged_waiting = True
            deps.clock.sleep(SCHEDULE_LOCK_POLL_SECONDS)

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
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
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
    for action in plan(state, now):
        gw = action.gameweek if action.gameweek is not None else "-"
        when = "due now" if action.at <= now else _fmt(action.at)
        typer.echo(f"  {action.job.value} gameweek={gw}: {when}")


def main() -> None:
    app(prog_name="python -m app.worker")
