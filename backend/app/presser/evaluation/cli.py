from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import CollectorError, ConfigError
from app.core.settings import load_settings
from app.db.engine import make_engine
from app.presser.evaluation.building import PseudonymisationError, build_real_cases, load_pseudonyms
from app.presser.evaluation.cases import (
    DEFAULT_CASES_PATH,
    HISTORY_PATH,
    PSEUDONYMS_PATH,
    load_cases,
    load_history,
    write_cases,
)
from app.presser.store import current_season

SEED = 20261009

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Evaluation of the presser writer: set building, runs, reviews and the summary.",
)


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group whatever the number of commands.
    pass


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


@dataclass(frozen=True)
class EvaluationCliDeps:
    engine: Engine | None


def _deps_from_settings() -> EvaluationCliDeps:
    try:
        engine: Engine | None = make_engine(load_settings().database_url)
    except ConfigError:
        engine = None  # `review`, `summary` and `evaluate` need no database
    return EvaluationCliDeps(engine=engine)


def get_deps(ctx: typer.Context) -> EvaluationCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def parse_gameweeks(value: str) -> list[int]:
    try:
        first, _, last = value.partition("-")
        start, end = int(first), int(last or first)
    except ValueError:
        raise fail("--gameweeks must look like 1-5") from None
    if start < 1 or end < start:
        raise fail("--gameweeks must look like 1-5")
    return list(range(start, end + 1))


@app.command(
    name="build-cases", help="Build the real cases from the database, pseudonymised, into set v1."
)
def build_cases_command(
    ctx: typer.Context,
    gameweeks: Annotated[str, typer.Option("--gameweeks", help="Range, e.g. 1-5.")] = "1-5",
    output: Annotated[Path, typer.Option("--output", help="The cases file.")] = DEFAULT_CASES_PATH,
    force: Annotated[
        bool, typer.Option("--force", help="Replace the real cases already in the file.")
    ] = False,
) -> None:
    deps = get_deps(ctx)
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    existing = load_cases(output) if output.exists() else []
    if any(case.source == "real" for case in existing) and not force:
        raise fail(f"{output.name} already holds real cases; use --force to replace them")
    history = load_history(HISTORY_PATH) if HISTORY_PATH.exists() else []
    with Session(deps.engine) as session:
        season = current_season(session)
    if season is None:
        raise fail("no season in the database")
    try:
        built = build_real_cases(
            deps.engine,
            season,
            parse_gameweeks(gameweeks),
            load_pseudonyms(PSEUDONYMS_PATH),
            history,
            SEED,
        )
    except PseudonymisationError as exc:
        raise fail(f"pseudonymisation failed: {exc}") from None
    except CollectorError as exc:
        raise fail(str(exc)) from None
    synthetic = [case for case in existing if case.source == "synthetic"]
    write_cases(output, [*built.cases, *synthetic])
    typer.echo(f"real cases: {len(built.cases)}  synthetic cases kept: {len(synthetic)}")
    for case in built.cases:
        typer.echo(f"  {case.id} ({case.split})")
    for alias, number in built.missing_history:
        typer.echo(f"missing history: {alias} GW{number}")


def main() -> None:
    app(prog_name="python -m app.presser.evaluation")
