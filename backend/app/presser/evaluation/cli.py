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
from app.presser.config import DEFAULT_PRESSER_MODEL, WRITER_TEMPERATURE, PresserSettings
from app.presser.evaluation.building import PseudonymisationError, build_real_cases, load_pseudonyms
from app.presser.evaluation.cases import (
    DEFAULT_CASES_PATH,
    DEFAULT_RESULTS_DIR,
    HISTORY_PATH,
    PSEUDONYMS_PATH,
    PresserCase,
    load_cases,
    load_history,
    write_cases,
)
from app.presser.evaluation.judge import JUDGE_MODEL, PresserJudge, build_presser_judge
from app.presser.evaluation.runner import (
    EvaluationError,
    default_result_path,
    format_totals,
    load_result,
    run_evaluation,
    write_result,
)
from app.presser.evaluation.summary import choose, format_run, summarise
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


def _caller(
    settings: PresserSettings, clock: Clock, model: str, temperature: float
) -> StructuredCaller:
    if settings.openrouter_api_key is None:
        raise ConfigError("OPENROUTER_API_KEY is not set")
    config = single_model_config(settings.openrouter_api_key, model)
    return StructuredCaller.from_spec(build_chat_model(config, temperature), load_prices(), clock)


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
        make_writer=lambda model: build_writer(_caller(settings, clock, model, WRITER_TEMPERATURE)),
        make_judge=lambda model: build_presser_judge(_caller(settings, clock, model, 0.0)),
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
    name="build-cases", help="Build the real cases from the database, pseudonymised, into set v2."
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


@app.command(help="Run a model over a split of set v2; the judge checks the faithfulness.")
def evaluate(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split", help="dev or test.")],
    model: Annotated[str, typer.Option("--model", help="Writer model ID.")] = DEFAULT_PRESSER_MODEL,
    judge_model: Annotated[str, typer.Option("--judge-model")] = JUDGE_MODEL,
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing run file and its reviews.")
    ] = False,
) -> None:
    if split not in ("dev", "test"):
        raise fail("--split must be dev or test")
    path = output or default_result_path(split, model)
    if path.exists() and not force:
        raise fail(
            f"{path} exists and may hold your reviews; pass --output or --force to overwrite"
        )
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
    write_result(path, data)
    typer.echo(format_totals(data))
    typer.echo(f"result: {path}")


RULE = "─" * 72
RATING_PROMPT = "rating 1-5 (s skip, q quit)"
NOTE_PROMPT = "note (empty for none)"
INFLECTION_PROMPT = "inflection errors in names (whole number, Enter = 0)"
VERDICT_PROMPT = "[a]gree  [f]lip  [s]kip  [q]uit"


def _facts_summary(case: PresserCase | None) -> str:
    if case is None:
        return "facts: (case not found)"
    facts = case.facts
    winners = ", ".join(f"{w.manager} {w.net_points}" for w in facts.winners)
    flops = ", ".join(f"{f.manager} {f.net_points}" for f in facts.flops)
    best = ", ".join(facts.captaincy.best[:3]) or "-"
    return f"winners: {winners}  flops: {flops}  best captain: {best}"


def _cases_by_id(cases_path: Path) -> dict[str, PresserCase]:
    return {case.id: case for case in load_cases(cases_path)} if cases_path.exists() else {}


def _load_run(run: Path) -> dict:
    try:
        return load_result(run)
    except EvaluationError as exc:
        raise fail(str(exc)) from None


@app.command(help="Rate the style of the pressers of a run, 1-5, with an optional note.")
def review(
    run: Annotated[Path, typer.Option("--run", help="The run file written by evaluate.")],
    all_: Annotated[bool, typer.Option("--all", help="Also the pressers already rated.")] = False,
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
) -> None:
    data = _load_run(run)
    cases = _cases_by_id(cases_path)
    pressers = data["pressers"]
    todo = [
        i
        for i, item in enumerate(pressers)
        if item["text"] is not None and (all_ or item["style"] is None)
    ]
    if not todo:
        typer.echo("nothing to review")
    for done, i in enumerate(todo):
        item = pressers[i]
        case = cases.get(item["case_id"])
        tags = ", ".join(case.tags) if case is not None and case.tags else "-"
        typer.echo(RULE)
        typer.echo(f"{done + 1}/{len(todo)}  id: {item['case_id']}  tags: {tags}")
        typer.echo(_facts_summary(case))
        typer.echo(item["text"])
        rating: int | None = None
        while rating is None:
            answer = typer.prompt(RATING_PROMPT).strip().lower()
            if answer == "q":
                _echo_rating_summary(pressers)
                return
            if answer == "s":
                break
            if answer in ("1", "2", "3", "4", "5"):
                rating = int(answer)
            else:
                typer.echo(f"unknown rating {answer!r}")
        if rating is None:
            continue
        note = typer.prompt(NOTE_PROMPT, default="", show_default=False).strip()
        item["style"] = {
            "rating": rating,
            "note": note or None,
            "inflection_errors": _ask_inflection_errors(),
        }
        write_result(run, data)
    _echo_rating_summary(pressers)


def _ask_inflection_errors() -> int:
    while True:
        answer = typer.prompt(INFLECTION_PROMPT, default="", show_default=False).strip()
        if not answer:
            return 0
        if answer.isdigit():
            return int(answer)
        typer.echo(f"unknown count {answer!r}")


def _echo_rating_summary(pressers: list[dict]) -> None:
    ratings = [p["style"]["rating"] for p in pressers if p["style"] is not None]
    average = f"{sum(ratings) / len(ratings):.2f}" if ratings else "n/a"
    counts = [
        p["style"]["inflection_errors"]
        for p in pressers
        if p["style"] is not None and "inflection_errors" in p["style"]
    ]
    inflection = f"{sum(counts) / len(counts):.2f}" if counts else "n/a"
    typer.echo(
        f"rated: {len(ratings)}/{len(pressers)}  average: {average}"
        f"  inflection errors: {inflection}"
    )


@app.command(name="judge-review", help="Give your own verdict on each claim the judge labelled.")
def judge_review(
    run: Annotated[Path, typer.Option("--run", help="The run file written by evaluate.")],
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
) -> None:
    data = _load_run(run)
    pressers = data["pressers"]
    todo = [
        i
        for i, item in enumerate(pressers)
        if any(claim["owner_label"] is None for claim in item["claims"])
    ]
    if limit is not None:
        todo = todo[:limit]
    if not todo:
        typer.echo("nothing to review")
    stop = False
    for i in todo:
        item = pressers[i]
        typer.echo(RULE)
        typer.echo(f"id: {item['case_id']}")
        typer.echo(item["text"])
        for claim in item["claims"]:
            if claim["owner_label"] is not None:
                continue
            typer.echo(f"claim: {claim['claim']}  (judge: {claim['label']})")
            while True:
                answer = typer.prompt(VERDICT_PROMPT).strip().lower()
                if answer == "a":
                    claim["owner_label"] = claim["label"]
                elif answer == "f":
                    claim["owner_label"] = (
                        "unsupported" if claim["label"] == "supported" else "supported"
                    )
                elif answer == "s":
                    pass
                elif answer == "q":
                    stop = True
                else:
                    typer.echo(f"unknown action {answer!r}")
                    continue
                break
            if stop:
                break
        write_result(run, data)
        if stop:
            break
    labelled = [
        claim for item in pressers for claim in item["claims"] if claim["owner_label"] is not None
    ]
    agreement = (
        sum(claim["owner_label"] == claim["label"] for claim in labelled) / len(labelled)
        if labelled
        else None
    )
    data["totals"]["reviewed_claims"] = len(labelled)
    data["totals"]["judge_agreement"] = agreement
    write_result(run, data)
    shown = "n/a" if agreement is None else f"{agreement:.2f}"
    typer.echo(f"reviewed claims: {len(labelled)}  agreement: {shown}")


@app.command(help="Compare the test-split runs and apply the pass rule.")
def summary(
    results_dir: Annotated[Path, typer.Option("--results-dir")] = DEFAULT_RESULTS_DIR,
) -> None:
    runs = summarise(results_dir) if results_dir.exists() else []
    if not runs:
        typer.echo("no test runs")
        return
    for run in runs:
        typer.echo(format_run(run))
    winner = choose(runs)
    typer.echo(f"winner: {winner}" if winner is not None else "no model passes")


def main() -> None:
    app(prog_name="python -m app.presser.evaluation")
