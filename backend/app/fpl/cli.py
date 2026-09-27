import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.core.errors import CollectorError
from app.core.settings import load_settings, parse_league_ids
from app.db.engine import make_engine
from app.fpl.backfill import backfill
from app.fpl.client import FplClient
from app.fpl.leagues import sync_leagues
from app.fpl.reference import sync_reference
from app.fpl.results import sync_results
from app.fpl.snapshot import take_deadline_snapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deps:
    engine: Engine
    client: FplClient
    league_ids_raw: str
    now: datetime


GameweekOption = Annotated[int, typer.Option(help="FPL gameweek number.")]

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="On-demand FPL collection jobs.",
)


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


def get_deps(ctx: typer.Context) -> Deps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _deps_from_settings() -> Deps:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = load_settings()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    return Deps(
        engine=make_engine(settings.database_url),
        client=FplClient(),
        league_ids_raw=settings.fpl_league_ids,
        now=datetime.now(UTC),
    )


# One transaction per command: a failure anywhere leaves the database as it was.
@contextmanager
def transaction(deps: Deps) -> Iterator[Session]:
    try:
        with Session(deps.engine) as session, session.begin():
            yield session
    except CollectorError as exc:
        raise fail(str(exc)) from None
    except SQLAlchemyError as exc:
        raise fail(f"database error ({type(exc).__name__})") from None


@app.command(
    "reference-sync",
    help="Sync the season, gameweeks, teams, players, fixtures and the flag change log.",
)
def reference_sync_command(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    with transaction(deps) as session:
        season = sync_reference(session, deps.client, deps.now)
    logger.info("reference sync: season=%s", season)


@app.command("deadline-snapshot", help="Capture every player's state before the gameweek deadline.")
def deadline_snapshot_command(ctx: typer.Context, gameweek: GameweekOption) -> None:
    deps = get_deps(ctx)
    with transaction(deps) as session:
        take_deadline_snapshot(session, deps.client, gameweek, deps.now)
    logger.info("deadline snapshot: gameweek=%d", gameweek)


@app.command(
    "league-sync",
    help="Sync the configured leagues' standings, managers and picks for a gameweek.",
)
def league_sync_command(ctx: typer.Context, gameweek: GameweekOption) -> None:
    deps = get_deps(ctx)
    with transaction(deps) as session:
        league_ids = parse_league_ids(deps.league_ids_raw)
        sync_reference(session, deps.client, deps.now)
        sync_leagues(session, deps.client, league_ids, [gameweek], deps.now)


@app.command("results-sync", help="Store the players' results for a finished and checked gameweek.")
def results_sync_command(ctx: typer.Context, gameweek: GameweekOption) -> None:
    deps = get_deps(ctx)
    with transaction(deps) as session:
        sync_reference(session, deps.client, deps.now)
        sync_results(session, deps.client, gameweek, deps.now)


@app.command(
    "backfill", help="Fill GW1 to the latest finished gameweek for the configured leagues."
)
def backfill_command(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    with transaction(deps) as session:
        league_ids = parse_league_ids(deps.league_ids_raw)
        backfill(session, deps.client, league_ids, deps.now)


def main() -> None:
    app(prog_name="python -m app.fpl")
