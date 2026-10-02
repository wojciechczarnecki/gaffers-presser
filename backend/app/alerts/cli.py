from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.alerts.config import AlertConfig, AlertSettings, parse_alert_config
from app.alerts.latency import format_report, has_alerts, latest_deadline_key, post_latencies
from app.alerts.schedule import deadline_key
from app.alerts.service import current_season
from app.alerts.status import alert_status
from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError
from app.core.local_time import format_local
from app.core.settings import load_settings
from app.corroboration.config import CorroborationSettings
from app.corroboration.runtime import build_runtime
from app.corroboration.service import CorroborationRuntime
from app.db.engine import make_engine
from app.llm.settings import load_llm_settings

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Pre-deadline alerts: status, latency report and preview.",
)


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group whatever the number of commands.
    pass


@dataclass(frozen=True)
class AlertsCliDeps:
    engine: Engine | None
    config: AlertConfig
    clock: Clock
    make_runtime: Callable[[], CorroborationRuntime]
    league_ids_raw: str = ""


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


def get_deps(ctx: typer.Context) -> AlertsCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _deps_from_settings() -> AlertsCliDeps:
    try:
        config = parse_alert_config(AlertSettings())
        corroboration_settings = load_llm_settings(CorroborationSettings)
        settings = load_settings()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    clock = SystemClock()
    return AlertsCliDeps(
        engine=make_engine(settings.database_url),
        config=config,
        clock=clock,
        make_runtime=lambda: build_runtime(corroboration_settings, clock),
        league_ids_raw=settings.fpl_league_ids,
    )


def db_engine(deps: AlertsCliDeps) -> Engine:
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    return deps.engine


@app.command(help="Show the current alert deadline, the next slot and the last alert.")
def status(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    state = alert_status(db_engine(deps), deps.config, deps.clock.now())
    if state.deadline is None:
        typer.echo("Alert deadline: none upcoming")
    else:
        kind = "rehearsal" if state.deadline.rehearsal else "real"
        typer.echo(
            f"Alert deadline: {state.deadline.key} ({kind})"
            f"  {format_local(state.deadline.deadline_at)} Europe/Warsaw"
        )
    if state.next_slot_at is not None:
        typer.echo(f"Next slot: {state.next_slot_kind} {format_local(state.next_slot_at)}")
    elif state.breaking_until is not None:
        typer.echo(f"Next slot: none, breaking until {format_local(state.breaking_until)}")
    else:
        typer.echo("Next slot: none")
    if state.last is None:
        typer.echo("Last alert: never")
    else:
        typer.echo(
            f"Last alert: {state.last.kind} {format_local(state.last.recorded_at)}"
            f" {state.last.status}"
        )
    typer.echo(f"Failed alerts: {state.failed}")


@app.command(help="Post to inbox latency of the alerts of one deadline.")
def latency(
    ctx: typer.Context,
    gameweek: Annotated[int | None, typer.Option("--gameweek", help="Gameweek number.")] = None,
    rehearsal: Annotated[bool, typer.Option("--rehearsal", help="The latest rehearsal.")] = False,
) -> None:
    deps = get_deps(ctx)
    engine = db_engine(deps)
    if gameweek is not None and rehearsal:
        raise fail("--gameweek and --rehearsal exclude each other")
    with Session(engine) as session:
        if gameweek is not None:
            season = current_season(session)
            key = deadline_key(season, gameweek) if season is not None else None
        else:
            key = latest_deadline_key(session, rehearsal)
        if key is None or not has_alerts(session, key):
            raise fail("no alerts recorded for that deadline")
        latencies = post_latencies(session, key)
    for line in format_report(key, latencies):
        typer.echo(line)


def main() -> None:
    app(prog_name="python -m app.alerts")
