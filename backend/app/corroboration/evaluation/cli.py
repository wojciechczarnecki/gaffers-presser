from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine

from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError, ConfigError
from app.core.settings import load_settings
from app.corroboration.config import CorroborationSettings
from app.corroboration.evaluation.building import TARGET_SIZE, build_cases
from app.corroboration.evaluation.cases import (
    DEFAULT_CASES_PATH,
    LABELS,
    PRELABEL_MODEL,
    JudgeCase,
    composition_problems,
    load_cases,
    render_case,
    write_cases,
)
from app.corroboration.evaluation.runner import (
    EvaluationError,
    default_result_path,
    format_metrics,
    run_evaluation,
    write_result,
)
from app.corroboration.judge import PROMPT_VERSION, Judge, build_judge
from app.db.engine import make_engine
from app.llm.chat import DEFAULT_MODEL, build_chat_model, single_model_config
from app.llm.pricing import Price, load_prices
from app.llm.settings import load_llm_settings
from app.llm.structured import StructuredCaller
from app.retrieval.config import resolve_embedding
from app.retrieval.embedder import Embedder, build_embedder

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Evaluation of the corroboration judge.",
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
    settings: CorroborationSettings
    clock: Clock
    make_embedder: Callable[[], Embedder]
    make_judge: Callable[[str], Judge]
    prices: dict[str, Price] | None = None


def _make_judge(settings: CorroborationSettings, clock: Clock) -> Callable[[str], Judge]:
    def make(model: str) -> Judge:
        if settings.openrouter_api_key is None:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        prices = load_prices()
        spec = build_chat_model(single_model_config(settings.openrouter_api_key, model))
        return build_judge(StructuredCaller.from_spec(spec, prices, clock))

    return make


def _make_embedder(settings: CorroborationSettings) -> Callable[[], Embedder]:
    def make() -> Embedder:
        config = resolve_embedding(settings)
        if config is None:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        return build_embedder(config)

    return make


def _deps_from_settings() -> EvaluationCliDeps:
    try:
        settings = load_llm_settings(CorroborationSettings)
    except ConfigError as exc:
        raise fail(str(exc)) from None
    try:
        engine: Engine | None = make_engine(load_settings().database_url)
    except ConfigError:
        engine = None  # `review` and `evaluate` need no database
    clock = SystemClock()
    return EvaluationCliDeps(
        engine=engine,
        settings=settings,
        clock=clock,
        make_embedder=_make_embedder(settings),
        make_judge=_make_judge(settings, clock),
    )


def get_deps(ctx: typer.Context) -> EvaluationCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _cost(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.6f}"


@app.command(name="build-cases", help="Build set v1 from the stored posts and pre-label it.")
def build_cases_command(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output")] = DEFAULT_CASES_PATH,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
    model: Annotated[str, typer.Option("--model", help="Pre-labelling model ID.")] = PRELABEL_MODEL,
    size: Annotated[int, typer.Option("--size", min=1)] = TARGET_SIZE,
    seed: Annotated[int, typer.Option("--seed")] = 7,
) -> None:
    if output.exists() and not force:
        raise fail(f"{output} already exists; pass --force to overwrite it")
    deps = get_deps(ctx)
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    try:
        judge = deps.make_judge(model)
        embedder = deps.make_embedder()
        prices = deps.prices if deps.prices is not None else load_prices()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    built = build_cases(deps.engine, embedder, judge, size=size, seed=seed, prices=prices)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_cases(output, built.cases)
    typer.echo(
        f"candidates: {built.candidates}  pre-labelled: {built.prelabelled}  failed: {built.failed}"
    )
    typer.echo(f"pre-labelled by: {model}")
    typer.echo(f"cost: {_cost(built.cost_usd)}")
    counts = Counter(case.expected for case in built.cases)
    typer.echo("labels: " + "  ".join(f"{label} {counts[label]}" for label in LABELS))
    splits = Counter(case.split for case in built.cases)
    typer.echo(f"cases: {len(built.cases)}  dev {splits['dev']}  test {splits['test']}")
    for problem in composition_problems(built.cases):
        typer.echo(f"problem: {problem}")
    typer.echo(f"written: {output}")


REVIEW_ACTIONS = "[a]ccept  [c]hange  [s]kip  [q]uit"
LABEL_PROMPT = "label (supports, contradicts, related or unrelated)"


@app.command(help="Review the pre-labelled cases one by one: accept or change the label.")
def review(
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    split: str | None = typer.Option(None, "--split"),
    case_id: str | None = typer.Option(None, "--id", help="One case, even a reviewed one."),
) -> None:
    # Needs no database and no LLM key, so it never builds the CLI dependencies.
    if split not in (None, "dev", "test"):
        raise fail("--split must be dev or test")
    cases = load_cases(cases_path)

    if case_id is not None:
        positions = [i for i, case in enumerate(cases) if case.id == case_id]
    else:
        positions = [i for i, case in enumerate(cases) if not case.reviewed]
    if split is not None:
        positions = [i for i in positions if cases[i].split == split]
    if case_id is not None and not positions:
        raise fail(f"no case with id {case_id}" + (f" in the {split} split" if split else ""))
    if not positions:
        typer.echo("nothing to review")
        return

    counts = {"accepted": 0, "changed": 0, "skipped": 0}
    interrupted = False
    try:
        _review_cases(cases, positions, cases_path, counts)
    except (typer.Abort, KeyboardInterrupt):
        interrupted = True  # every accepted or changed case is already on disk

    reviewed = sum(1 for case in cases if case.reviewed)
    typer.echo("── summary " + "─" * 61)
    typer.echo(
        f"accepted: {counts['accepted']}  changed: {counts['changed']}"
        f"  skipped: {counts['skipped']}"
    )
    typer.echo(f"reviewed: {reviewed}/{len(cases)}")
    if interrupted:
        raise typer.Exit(130)


def _ask_label() -> str:
    while True:
        label = typer.prompt(LABEL_PROMPT).strip().lower()
        if label in LABELS:
            return label
        typer.echo(f"unknown label {label!r}")


def _review_cases(
    cases: list[JudgeCase], positions: list[int], cases_path: Path, counts: dict[str, int]
) -> None:
    for done, i in enumerate(positions):
        show = True
        while True:
            if show:
                reviewed = sum(1 for case in cases if case.reviewed)
                progress = (
                    f"{reviewed}/{len(cases)} reviewed, {len(positions) - done} left in this run"
                )
                typer.echo(render_case(cases[i], progress))
            show = False
            action = typer.prompt(REVIEW_ACTIONS).strip().lower()
            if action == "a":
                cases[i] = cases[i].model_copy(update={"reviewed": True})
                write_cases(cases_path, cases)
                counts["accepted"] += 1
                break
            if action == "c":
                label = _ask_label()
                cases[i] = cases[i].model_copy(update={"expected": label, "reviewed": True})
                write_cases(cases_path, cases)
                counts["changed"] += 1
                typer.echo("saved")
                break
            if action == "s":
                counts["skipped"] += 1
                break
            if action == "q":
                return
            typer.echo(f"unknown action {action!r}")


@app.command(help="Run the judge over a split of set v1 and report its metrics.")
def evaluate(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split")] = "test",
    model: Annotated[str | None, typer.Option("--model", help="Chat model ID.")] = None,
    include_unreviewed: Annotated[bool, typer.Option("--include-unreviewed")] = False,
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    if split not in ("dev", "test"):
        raise fail("--split must be dev or test")
    deps = get_deps(ctx)
    chosen_model = model or deps.settings.llm_model or DEFAULT_MODEL
    cases = load_cases(cases_path)
    try:
        judge = deps.make_judge(chosen_model)
    except CollectorError as exc:
        raise fail(str(exc)) from None
    try:
        data = run_evaluation(
            cases,
            judge,
            split,
            include_unreviewed,
            chosen_model,
            PROMPT_VERSION,
            deps.clock.now(),
        )
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    path = output or default_result_path(split, chosen_model)
    write_result(path, data)
    typer.echo(format_metrics(data))
    typer.echo(f"result: {path}")


def main() -> None:
    app(prog_name="python -m app.corroboration.evaluation")
