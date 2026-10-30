import json
import shutil
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, SystemClock
from app.core.errors import AppError, ConfigError
from app.core.eval_results import ResultExistsError, check_new_result, make_header
from app.core.settings import load_settings
from app.corroboration.config import CorroborationSettings, judge_model
from app.corroboration.evaluation.building import (
    MAX_INPUTS,
    TARGET_SIZE,
    build_cases,
    write_candidate_files,
)
from app.corroboration.evaluation.cases import (
    DEFAULT_CASES_PATH,
    DEFAULT_WIDTH,
    LABELS,
    PRELABEL_MODEL,
    V1_CASES_PATH,
    V2_SET_DIR,
    JudgeCase,
    composition_problems,
    load_candidates,
    load_cases,
    prelabel_agreement,
    prelabels_disagree,
    render_case,
    review_order,
    session_label,
    write_cases,
)
from app.corroboration.evaluation.judging import (
    JudgingError,
    estimate_export,
    export_inputs,
    import_verdicts,
)
from app.corroboration.evaluation.runner import (
    EvaluationError,
    calls_per_month,
    default_result_path,
    format_metrics,
    run_evaluation,
    write_result,
)
from app.corroboration.evaluation.scratch import (
    ScratchError,
    guard_scratch_url,
    run_build_scratch,
    window_for,
)
from app.corroboration.judge import PROMPT_VERSION, Judge, build_judge
from app.db.engine import make_engine
from app.extraction.evaluation.dataset import load_context
from app.extraction.service import ExtractionRuntime, load_reference_files
from app.llm.chat import (
    PROVIDER,
    build_chat_model,
    resolve_llm,
    single_model_config,
)
from app.llm.models import reasoning_effort_of
from app.llm.pricing import Price, load_prices
from app.llm.settings import load_llm_settings
from app.llm.structured import StructuredCaller
from app.retrieval.config import resolve_embedding
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.evaluation.dataset import load_corpus
from app.retrieval.indexing import IndexingRuntime

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
    output: Annotated[Path, typer.Option("--output")] = V1_CASES_PATH,
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
    except AppError as exc:
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


RETRIEVAL_V2_DIR = Path(__file__).resolve().parents[3] / "evals" / "retrieval" / "v2"
EXTRACTION_V2_DIR = Path(__file__).resolve().parents[3] / "evals" / "extraction" / "v2"


def _scratch_engine(url: str) -> Engine:
    try:
        configured: str | None = load_settings().database_url
    except ConfigError:
        configured = None
    try:
        guard_scratch_url(url, configured)
    except ScratchError as exc:
        raise fail(str(exc)) from None
    return make_engine(url)


@app.command(
    name="build-scratch",
    help="Load the retrieval v2 corpus into a throwaway database, embed and extract it.",
)
def build_scratch(
    ctx: typer.Context,
    database_url: Annotated[str, typer.Option("--database-url")],
    corpus_path: Annotated[Path, typer.Option("--corpus")] = RETRIEVAL_V2_DIR / "corpus.jsonl",
    context_dir: Annotated[Path, typer.Option("--context")] = EXTRACTION_V2_DIR,
    retrieval_set: Annotated[Path, typer.Option("--retrieval-set")] = RETRIEVAL_V2_DIR,
    gameweeks: Annotated[list[int] | None, typer.Option("--gameweek")] = None,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd")] = 0.90,
) -> None:
    engine = _scratch_engine(database_url)
    try:
        settings = (
            ctx.obj.settings if ctx.obj is not None else load_llm_settings(CorroborationSettings)
        )
        embedding = resolve_embedding(settings)
        llm_config = resolve_llm(settings)
        if embedding is None or llm_config is None:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        prices, aliases = load_reference_files()
    except AppError as exc:
        raise fail(str(exc)) from None
    players, teams, fixtures = load_context(context_dir)
    handles = json.loads((retrieval_set / "set.json").read_text())["list_handles"]
    clock = SystemClock()
    embedder = build_embedder(embedding)
    try:
        summary = run_build_scratch(
            engine,
            load_corpus(corpus_path),
            handles,
            players,
            teams,
            fixtures,
            embedder=embedder,
            indexing=IndexingRuntime(
                model=embedding.model,
                make_embedder=lambda: embedder,
                prices=embedding.prices,
                tracing=None,
                clock=clock,
            ),
            extraction=ExtractionRuntime(
                provider=PROVIDER,
                model=llm_config.model,
                fallback_model=llm_config.fallback_model,
                make_spec=lambda: build_chat_model(llm_config),
                tracing=None,
                prices=prices,
                aliases=aliases,
            ),
            gameweeks=gameweeks or [6, 7],
            max_cost_usd=max_cost_usd,
            clock=clock,
            stop=threading.Event(),
        )
    except AppError as exc:
        raise fail(str(exc)) from None
    typer.echo(f"seeded: {summary.seeded}  embedded: {summary.embedded}")
    for window in summary.windows:
        typer.echo(
            f"window GW{window.gameweek}: {window.start.isoformat()} .. {window.end.isoformat()}"
        )
    typer.echo(f"extracted: {summary.extract.extracted}  failed: {summary.extract.failed}")
    typer.echo(f"embeddings cost: {_cost(summary.embeddings_cost_usd)}")
    typer.echo(f"extraction cost: {_cost(summary.extraction_cost_usd)}")
    if summary.extract.stopped_by_cap:
        raise fail(f"the cost cap of ${max_cost_usd:.2f} stopped the extraction before the end")


@app.command(
    name="build-candidates",
    help="Draw the anchors and retrieved posts of the GW windows from the scratch database.",
)
def build_candidates_command(
    ctx: typer.Context,
    database_url: Annotated[str, typer.Option("--database-url")],
    out: Annotated[Path, typer.Option("--out")],
    set_dir: Annotated[Path, typer.Option("--set")] = V2_SET_DIR,
    retrieval_set: Annotated[Path, typer.Option("--retrieval-set")] = RETRIEVAL_V2_DIR,
    gameweeks: Annotated[list[int] | None, typer.Option("--gameweek")] = None,
    max_inputs: Annotated[int, typer.Option("--max-inputs", min=1)] = MAX_INPUTS,
    seed: Annotated[int, typer.Option("--seed")] = 19,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    engine = _scratch_engine(database_url)
    try:
        settings = (
            ctx.obj.settings if ctx.obj is not None else load_llm_settings(CorroborationSettings)
        )
        embedding = resolve_embedding(settings)
        if embedding is None:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        with Session(engine) as session:
            windows = [window_for(session, gameweek) for gameweek in (gameweeks or [6, 7])]
        embedder = build_embedder(embedding)
        count, info = write_candidate_files(
            engine,
            embedder,
            embedding.prices,
            windows=windows,
            set_dir=set_dir,
            out=out,
            list_handles=json.loads((retrieval_set / "set.json").read_text())["list_handles"],
            corpus="evals/retrieval/v2/corpus.jsonl",
            max_inputs=max_inputs,
            seed=seed,
            force=force,
        )
    except AppError as exc:
        raise fail(str(exc)) from None
    typer.echo(f"candidates: {info.candidates}  written: {count} to {out}")
    typer.echo(f"judge calls a month: {info.judge_calls_per_month:.0f}")
    typer.echo(
        f"build cost: embeddings {_cost(info.embeddings_cost_usd)}"
        f"  extraction {_cost(info.extraction_cost_usd)}"
        f"  queries {_cost(info.query_embeddings_cost_usd)}"
    )


@app.command(name="export-judge-inputs", help="Export the session judge's inputs.")
def export_judge_inputs(
    out: Annotated[Path, typer.Option("--out")],
    seed: Annotated[int, typer.Option("--seed")] = 19,
    cases_path: Annotated[Path | None, typer.Option("--cases")] = None,
    candidates_path: Annotated[Path | None, typer.Option("--candidates")] = None,
) -> None:
    if (cases_path is None) == (candidates_path is None):
        raise fail("pass exactly one of --cases and --candidates")
    try:
        if cases_path is not None:
            count = export_inputs(
                cases=load_cases(cases_path),
                candidates=[],
                out=out,
                seed=seed,
                cases_source=cases_path.resolve(),
            )
        else:
            assert candidates_path is not None
            count = export_inputs(
                cases=[],
                candidates=load_candidates(candidates_path),
                out=out,
                seed=seed,
                candidates_source=candidates_path.resolve(),
            )
    except JudgingError as exc:
        raise fail(str(exc)) from None
    typer.echo(f"exported {count} inputs to {out}")


@app.command(help="Print the calls and tokens the session judge needs for an export.")
def estimate(export: Annotated[Path, typer.Option("--export")]) -> None:
    try:
        result = estimate_export(export)
    except JudgingError as exc:
        raise fail(str(exc)) from None
    typer.echo(f"calls: {result.calls}  estimated tokens: {result.tokens}")


@app.command(name="import-judge", help="Import the session judge's verdicts into a set.")
def import_judge(
    export: Annotated[Path, typer.Option("--export")],
    set_dir: Annotated[Path, typer.Option("--set")],
    judge_model: Annotated[str, typer.Option("--judge-model")],
    select: Annotated[int | None, typer.Option("--select", min=1)] = None,
    seed: Annotated[int, typer.Option("--seed")] = 19,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    try:
        report = import_verdicts(
            export, set_dir, judge_model, select=select, seed=seed, force=force
        )
    except JudgingError as exc:
        raise fail(str(exc)) from None
    typer.echo(
        f"v1 cases pre-labelled: {report.copied}  candidates: {report.candidates}"
        f"  new cases: {report.new}"
    )
    if report.skipped_without_verdict:
        typer.echo(f"skipped without a verdict: {len(report.skipped_without_verdict)}")
    if report.skipped_in_set:
        typer.echo(f"skipped, already in the set: {len(report.skipped_in_set)}")
    cases = load_cases(set_dir / "cases.jsonl")
    counts = Counter(case.expected for case in cases)
    typer.echo("labels: " + "  ".join(f"{label} {counts[label]}" for label in LABELS))
    for problem in composition_problems(cases):
        typer.echo(f"problem: {problem}")


REVIEW_ACTIONS = (
    "[1] supports  [2] contradicts  [3] related  [4] unrelated"
    "  [a]ccept session pre-label (or Enter)  [s]kip  [b]ack  [q]uit"
)
KEY_LABELS = {
    str(n): label for n, label in enumerate(("supports", "contradicts", "related", "unrelated"), 1)
}


@app.command(help="Label cases with keys 1-4; a or Enter accepts the session pre-label.")
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
        positions = review_order(cases, [i for i, case in enumerate(cases) if not case.reviewed])
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


def _review_cases(
    cases: list[JudgeCase], positions: list[int], cases_path: Path, counts: dict[str, int]
) -> None:
    width = min(shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns, 100)
    done = 0
    while done < len(positions):
        i = positions[done]
        reviewed = sum(1 for case in cases if case.reviewed)
        progress = f"{reviewed}/{len(cases)} reviewed, {len(positions) - done} left in this run"
        typer.echo(render_case(cases[i], progress, width))
        typer.echo("")
        pick = _ask(REVIEW_ACTIONS, {*KEY_LABELS, "a", "", "s", "b", "q"})
        if pick == "q":
            return
        if pick == "s":
            counts["skipped"] += 1
            done += 1
            continue
        if pick == "b":
            done = max(done - 1, 0)
            continue
        if pick in ("a", ""):
            if prelabels_disagree(cases[i]):
                typer.echo("the pre-labels disagree: accept is refused, pick with 1-4")
                continue
            label = session_label(cases[i])
        else:
            label = KEY_LABELS[pick]
        counts["accepted" if label == session_label(cases[i]) else "changed"] += 1
        cases[i] = cases[i].model_copy(update={"expected": label, "reviewed": True})
        write_cases(cases_path, cases)
        typer.echo(f"saved: {label}")
        done += 1


def _ask(prompt: str, valid: set[str]) -> str:
    while True:
        answer = typer.prompt(prompt, default="", show_default=False).strip().lower()
        if answer in valid:
            return answer
        typer.echo(f"unknown key {answer!r}")


@app.command(help="Count how often the owner's reviewed labels differ from each pre-labeller.")
def agreement(
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
) -> None:
    result = prelabel_agreement(load_cases(cases_path))
    typer.echo(f"reviewed: {result.reviewed}")
    for judge, (total, differ) in result.by_judge.items():
        typer.echo(f"{judge}: {differ} of {total} differ")
    total, differ = result.v1
    typer.echo(f"{PRELABEL_MODEL}: {differ} of {total} differ")
    for line in result.differing:
        typer.echo(line)


@app.command(help="Run the judge over a split of a set and report its metrics and the verdict.")
def evaluate(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split")] = "test",
    model: Annotated[str | None, typer.Option("--model", help="Chat model ID.")] = None,
    include_unreviewed: Annotated[bool, typer.Option("--include-unreviewed")] = False,
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    if split not in ("dev", "test"):
        raise fail("--split must be dev or test")
    deps = get_deps(ctx)
    chosen_model = model or judge_model(deps.settings)
    path = output or default_result_path(split, chosen_model, cases_path.parent.name)
    try:
        check_new_result(path, force)
    except ResultExistsError as exc:
        raise fail(str(exc)) from None
    cases = load_cases(cases_path)
    try:
        judge = deps.make_judge(chosen_model)
    except AppError as exc:
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
            calls_per_month=calls_per_month(cases_path.parent),
            usd_pln_rate=deps.settings.usd_pln_rate,
        )
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    data["header"] = make_header(
        set_version=cases_path.parent.name,
        model=chosen_model,
        prompt_version=PROMPT_VERSION,
        reasoning_effort=reasoning_effort_of(chosen_model),
        judge=None,
        now=deps.clock.now(),
    )
    write_result(path, data)
    typer.echo(format_metrics(data))
    typer.echo(f"result: {path}")


def main() -> None:
    app(prog_name="python -m app.corroboration.evaluation")
