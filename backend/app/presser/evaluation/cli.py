from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError, ConfigError
from app.core.settings import load_settings
from app.db.engine import make_engine
from app.llm.chat import build_chat_model, single_model_config
from app.llm.pricing import load_prices
from app.llm.settings import load_llm_settings
from app.llm.structured import StructuredCaller
from app.presser.config import DEFAULT_PRESSER_MODEL, PresserSettings
from app.presser.evaluation.building import PseudonymisationError, build_real_cases, load_pseudonyms
from app.presser.evaluation.cases import (
    DEFAULT_CASES_PATH,
    HISTORY_PATH,
    PSEUDONYMS_PATH,
    load_cases,
    load_history,
    write_cases,
)
from app.presser.evaluation.judge import JUDGE_MODEL, PresserJudge, build_presser_judge
from app.presser.evaluation.runner import (
    EvaluationError,
    default_result_path,
    format_totals,
    run_evaluation,
    write_result,
)
from app.presser.store import current_season
from app.presser.writer import Writer, build_writer

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
    clock: Clock
    make_writer: Callable[[str], Writer]
    make_judge: Callable[[str], PresserJudge]


def _caller(settings: PresserSettings, clock: Clock, model: str) -> StructuredCaller:
    if settings.openrouter_api_key is None:
        raise ConfigError("OPENROUTER_API_KEY is not set")
    config = single_model_config(settings.openrouter_api_key, model)
    return StructuredCaller.from_spec(build_chat_model(config), load_prices(), clock)


def _deps_from_settings() -> EvaluationCliDeps:
    try:
        settings = load_llm_settings(PresserSettings)
    except ConfigError as exc:
        raise fail(str(exc)) from None
    try:
        engine: Engine | None = make_engine(load_settings().database_url)
    except ConfigError:
        engine = None  # only `build-cases` needs the database
    clock = SystemClock()
    return EvaluationCliDeps(
        engine=engine,
        clock=clock,
        make_writer=lambda model: build_writer(_caller(settings, clock, model)),
        make_judge=lambda model: build_presser_judge(_caller(settings, clock, model)),
    )


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


@app.command(help="Run a model over a split of set v1; the judge checks the faithfulness.")
def evaluate(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split", help="dev or test.")],
    model: Annotated[str, typer.Option("--model", help="Writer model ID.")] = DEFAULT_PRESSER_MODEL,
    judge_model: Annotated[str, typer.Option("--judge-model")] = JUDGE_MODEL,
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    if split not in ("dev", "test"):
        raise fail("--split must be dev or test")
    deps = get_deps(ctx)
    cases = load_cases(cases_path)
    try:
        writer = deps.make_writer(model)
        judge = deps.make_judge(judge_model)
    except CollectorError as exc:
        raise fail(str(exc)) from None
    try:
        data = run_evaluation(cases, writer, judge, split, model, judge_model, deps.clock.now())
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    path = output or default_result_path(split, model)
    write_result(path, data)
    typer.echo(format_totals(data))
    typer.echo(f"result: {path}")


def main() -> None:
    app(prog_name="python -m app.presser.evaluation")
