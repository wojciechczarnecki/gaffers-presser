from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError
from app.core.local_time import format_local
from app.core.settings import load_settings, parse_league_ids
from app.db.engine import make_engine
from app.delivery.channels import build_channel
from app.delivery.config import DeliverySettings, resolve_delivery
from app.delivery.service import DeliveryService
from app.llm.chat import build_chat_model
from app.llm.pricing import load_prices
from app.llm.settings import load_llm_settings
from app.llm.structured import StructuredCaller
from app.llm.tracing import resolve_tracing
from app.presser.config import (
    PresserSettings,
    parse_nicknames,
    presser_disabled_reason,
    resolve_presser_llm,
)
from app.presser.facts import NoFactsError, build_fact_sheet
from app.presser.service import (
    PresserFailed,
    PresserRuntime,
    generate_presser,
    send_presser,
)
from app.presser.store import current_season, presser_status
from app.presser.tracing import make_presser_tracer
from app.presser.writer import build_writer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="League pressers: fact sheet, preview, manual send and status.",
)


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group whatever the number of commands.
    pass


@dataclass(frozen=True)
class PresserCliDeps:
    engine: Engine
    settings: PresserSettings
    nicknames: dict[int, str]
    make_runtime: Callable[[bool], PresserRuntime]
    clock: Clock
    league_ids_raw: str = ""
    delivery_enabled: bool = True


LeagueOption = Annotated[int, typer.Option("--league", help="FPL league ID.")]
GameweekOption = Annotated[int, typer.Option("--gameweek", help="Gameweek number.")]


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


def get_deps(ctx: typer.Context) -> PresserCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _deps_from_settings() -> PresserCliDeps:
    try:
        settings = load_llm_settings(PresserSettings)
        nicknames = parse_nicknames(settings)
        delivery_config = resolve_delivery(DeliverySettings())
        app_settings = load_settings()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    engine = make_engine(app_settings.database_url)
    clock = SystemClock()

    def make_runtime(with_delivery: bool) -> PresserRuntime:
        llm = resolve_presser_llm(settings)
        assert llm is not None
        caller = StructuredCaller.from_spec(build_chat_model(llm), load_prices(), clock)
        delivery = None
        if with_delivery:
            assert delivery_config is not None
            delivery = DeliveryService(engine, build_channel(delivery_config), clock)
        try:
            league_ids = parse_league_ids(app_settings.fpl_league_ids)
        except CollectorError:
            league_ids = []
        return PresserRuntime(
            writer=build_writer(caller),
            model=llm.model,
            tracer=make_presser_tracer(resolve_tracing(settings)),
            nicknames=nicknames,
            delivery=delivery,
            league_ids=league_ids,
            clock=clock,
        )

    return PresserCliDeps(
        engine=engine,
        settings=settings,
        nicknames=nicknames,
        make_runtime=make_runtime,
        clock=clock,
        league_ids_raw=app_settings.fpl_league_ids,
        delivery_enabled=delivery_config is not None,
    )


def _season(deps: PresserCliDeps) -> str:
    with Session(deps.engine) as session:
        season = current_season(session)
    if season is None:
        raise fail("no season in the database")
    return season


def _require_enabled(deps: PresserCliDeps, *, delivery: bool) -> None:
    try:
        reason = presser_disabled_reason(
            deps.settings, delivery=deps.delivery_enabled or not delivery
        )
    except CollectorError as exc:
        raise fail(str(exc)) from None
    if reason is not None:
        raise fail(f"presser disabled: {reason}")


@app.command(help="Print the fact sheet of a league and gameweek.")
def facts(ctx: typer.Context, league: LeagueOption, gameweek: GameweekOption) -> None:
    deps = get_deps(ctx)
    season = _season(deps)
    try:
        with Session(deps.engine) as session:
            sheet = build_fact_sheet(session, season, league, gameweek, deps.nicknames)
    except CollectorError as exc:
        raise fail(type(exc).__name__ + ": " + str(exc)) from None
    typer.echo(sheet.model_dump_json(indent=2))


@app.command(help="Generate a presser and print it; nothing is sent or marked as sent.")
def preview(ctx: typer.Context, league: LeagueOption, gameweek: GameweekOption) -> None:
    deps = get_deps(ctx)
    _require_enabled(deps, delivery=False)
    season = _season(deps)
    runtime = deps.make_runtime(False)
    try:
        generated = generate_presser(deps.engine, runtime, season, league, gameweek)
    except NoFactsError as exc:
        raise fail(f"no facts: {exc}") from None
    except PresserFailed as exc:
        raise fail(f"the writer failed: {exc}") from None
    typer.echo(generated.text)
    cost = "unknown" if generated.cost_usd is None else f"${generated.cost_usd:.4f}"
    typer.echo(f"chars={len(generated.text)} cost={cost} latency={generated.latency_seconds:.1f}s")


@app.command(help="Generate and send a presser by hand, also for an older gameweek.")
def send(ctx: typer.Context, league: LeagueOption, gameweek: GameweekOption) -> None:
    deps = get_deps(ctx)
    _require_enabled(deps, delivery=True)
    season = _season(deps)
    runtime = deps.make_runtime(True)
    try:
        result = send_presser(
            deps.engine, runtime, season, league, gameweek, skip_statuses=("sent",)
        )
    except NoFactsError as exc:
        raise fail(f"no facts: {exc}") from None
    except PresserFailed as exc:
        raise fail(f"the writer failed: {exc}") from None
    typer.echo("already_sent" if result == "skipped" else result)
    if result == "failed":
        raise typer.Exit(1)


@app.command(help="Show whether the presser is enabled and the latest presser per league.")
def status(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    try:
        reason = presser_disabled_reason(deps.settings, delivery=deps.delivery_enabled)
    except CollectorError as exc:
        raise fail(str(exc)) from None
    if reason is None:
        typer.echo(f"Presser: enabled (model={deps.settings.presser_model})")
    else:
        typer.echo(f"Presser: disabled ({reason})")
    try:
        league_ids = parse_league_ids(deps.league_ids_raw)
    except CollectorError:
        league_ids = []
    with Session(deps.engine) as session:
        season = current_season(session)
        statuses = presser_status(session, season, league_ids) if season is not None else []
    for item in statuses:
        if item.latest is None:
            typer.echo(f"League {item.league_id}: no presser yet  failed: {item.failed}")
            continue
        cost = "unknown" if item.latest.cost_usd is None else f"${item.latest.cost_usd:.4f}"
        typer.echo(
            f"League {item.league_id}: GW{item.latest.gameweek} {item.latest.status}"
            f" {format_local(item.latest.created_at)} Europe/Warsaw"
            f" {item.latest.model} {cost}  failed: {item.failed}"
        )


def main() -> None:
    app(prog_name="python -m app.presser")
