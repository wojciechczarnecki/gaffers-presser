import json
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import CollectorError, ConfigError
from app.core.settings import ExtractionSettings, load_extraction_settings, load_settings
from app.db.engine import make_engine
from app.extraction.config import resolve_llm, resolve_tracing
from app.extraction.evaluation.cases import (
    EvalCase,
    ExpectedEvent,
    load_cases,
    parse_cases,
    write_cases,
)
from app.extraction.evaluation.compare import compare_sets
from app.extraction.evaluation.review import (
    PlayerDirectory,
    case_to_json,
    editor_command,
    parse_edited,
    render_case,
    render_player,
    run_editor,
)
from app.extraction.evaluation.runner import run_evaluation
from app.extraction.flow import PROMPT_VERSION, build_flow
from app.extraction.linking import PlayerIndex, load_aliases, load_players, load_snapshot
from app.extraction.providers import ChatModelSpec, build_chat_model
from app.extraction.service import (
    ExtractionRuntime,
    extract_post,
    load_reference_files,
    run_with_retries,
)
from app.extraction.store import posts_for_reextract
from app.extraction.tracing import flush, make_handler, run_config
from app.tweets.loop import Clock
from app.worker.loop import SystemClock

app = typer.Typer(add_completion=False, no_args_is_help=True, help="The extraction toolkit.")

BuildSpec = Callable[..., ChatModelSpec]


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group even while it only has one command yet
    # (`reextract`): later steps add `snapshot-players`, `prelabel`, `evaluate`.
    pass


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


@dataclass(frozen=True)
class ExtractionCliDeps:
    engine: Engine | None
    settings: ExtractionSettings
    build_spec: BuildSpec
    clock: Clock


def build_spec_from_settings(settings: ExtractionSettings) -> BuildSpec:
    def build_spec(model: str | None, *, fallback: bool) -> ChatModelSpec:
        config = resolve_llm(settings, model, use_fallback=fallback)
        if config is None:
            raise ConfigError("OPENROUTER_API_KEY must be set")
        return build_chat_model(config)

    return build_spec


def db_engine(deps: ExtractionCliDeps) -> Engine:
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    return deps.engine


def get_deps(ctx: typer.Context) -> ExtractionCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _engine_from_env() -> Engine | None:
    try:
        return make_engine(load_settings().database_url)
    except ConfigError:
        return None  # `evaluate` needs no database; the other commands say so through db_engine


def _deps_from_settings() -> ExtractionCliDeps:
    try:
        settings = load_extraction_settings()
    except ConfigError as exc:
        raise fail(str(exc)) from None
    return ExtractionCliDeps(
        engine=_engine_from_env(),
        settings=settings,
        build_spec=build_spec_from_settings(settings),
        clock=SystemClock(),
    )


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@app.command(help="Re-extract posts, optionally with another model.")
def reextract(
    ctx: typer.Context,
    x_id: int | None = typer.Option(None, "--x-id"),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    failed: bool = typer.Option(False, "--failed"),
    model: str | None = typer.Option(None, "--model"),
) -> None:
    deps = get_deps(ctx)

    selectors = [x_id is not None, since is not None or until is not None, failed]
    if sum(1 for selected in selectors if selected) != 1:
        raise fail("exactly one of --x-id, --since/--until, --failed must be given")
    if (since is not None) != (until is not None):
        raise fail("--since and --until must be given together")

    try:
        spec = deps.build_spec(model, fallback=True)
        prices, aliases = load_reference_files()
    except CollectorError as exc:
        raise fail(str(exc)) from None

    since_dt = _parse_iso(since) if since is not None else None
    until_dt = _parse_iso(until) if until is not None else None

    with Session(db_engine(deps)) as session:
        posts = posts_for_reextract(
            session, x_id=x_id, since=since_dt, until=until_dt, failed=failed
        )

    tracing = resolve_tracing(deps.settings)
    handler = make_handler(tracing)
    runtime = ExtractionRuntime(
        provider=spec.provider,
        model=spec.model,
        make_spec=lambda: spec,
        tracing=tracing,
        prices=prices,
        aliases=aliases,
    )
    stop_event = threading.Event()

    processed = 0
    events = 0
    failures = 0
    total_cost = 0.0
    any_cost = False
    try:
        for post in posts:
            outcome = extract_post(
                db_engine(deps),
                runtime,
                post,
                deps.clock,
                stop_event,
                handler,
                record_latency=False,
            )
            if outcome is None:
                continue
            processed += 1
            if outcome.status == "failed":
                failures += 1
            else:
                events += outcome.events
            if outcome.cost_usd is not None:
                any_cost = True
                total_cost += outcome.cost_usd
    finally:
        flush(handler)

    typer.echo(f"posts processed: {processed}")
    typer.echo(f"events: {events}")
    typer.echo(f"failures: {failures}")
    typer.echo(f"total cost: {f'${total_cost:.4f}' if any_cost else 'n/a'}")


@app.command("snapshot-players", help="Write the latest season's players and teams as JSON.")
def snapshot_players(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    deps = get_deps(ctx)
    with Session(db_engine(deps)) as session:
        players, teams = load_players(session)
    if not players:
        raise fail("no players in the database")
    payload = {
        "season": players[0].season,
        "players": [asdict(p) for p in sorted(players, key=lambda p: p.fpl_id)],
        "teams": [asdict(t) for t in sorted(teams, key=lambda t: t.fpl_id)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    typer.echo(f"players: {len(players)}")


@app.command(help="Pre-label local posts with a model into candidate evaluation cases.")
def prelabel(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output")],
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    limit: int | None = typer.Option(None, "--limit"),
    model: str | None = typer.Option(None, "--model"),
    eval_set: Annotated[Path | None, typer.Option("--eval-set")] = None,
) -> None:
    deps = get_deps(ctx)
    try:
        spec = deps.build_spec(model, fallback=False)
    except CollectorError as exc:
        raise fail(str(exc)) from None

    existing = load_cases(output) if output.exists() else []
    known_ids = {case.id for case in existing}
    if eval_set is not None:
        known_ids |= {case.id for case in load_cases(eval_set)}

    since_dt = _parse_iso(since) if since is not None else datetime(1970, 1, 1, tzinfo=UTC)
    until_dt = _parse_iso(until) if until is not None else datetime(9999, 1, 1, tzinfo=UTC)
    with Session(db_engine(deps)) as session:
        posts = posts_for_reextract(session, since=since_dt, until=until_dt)
        players, teams = load_players(session)
    player_aliases, team_aliases = load_aliases()
    flow = build_flow(spec, PlayerIndex(players, teams, player_aliases, team_aliases))

    skipped = sum(1 for post in posts if str(post.x_id) in known_ids)
    pending = [post for post in posts if str(post.x_id) not in known_ids]
    if limit is not None:
        pending = pending[:limit]

    stop_event = threading.Event()
    new_cases: list[EvalCase] = []
    failures = 0
    for post in pending:
        config = run_config(post.x_id, PROMPT_VERSION, spec.provider, spec.model, None)
        outcome = run_with_retries(flow, post, config, deps.clock, stop_event)
        if outcome.result is None:
            failures += 1
            continue
        new_cases.append(
            EvalCase(
                id=str(post.x_id),
                author_handle=post.author_handle,
                text=post.text,
                created_at=post.created_at,
                is_repost=post.is_repost,
                is_reply=post.is_reply,
                split="dev" if post.x_id % 10 < 3 else "test",
                synthetic=False,
                reviewed=False,
                tags=[],
                expected_events=[
                    ExpectedEvent(
                        mention=event.mention,
                        fpl_id=event.player_fpl_id,
                        event_type=event.event_type,
                        certainty=event.certainty,
                    )
                    for event in outcome.result.events
                ],
            )
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    write_cases(output, [*existing, *new_cases])
    typer.echo(f"cases written: {len(new_cases)}")
    typer.echo(f"skipped: {skipped}")
    typer.echo(f"failures: {failures}")


EVALS_DIR = Path(__file__).resolve().parents[2] / "evals" / "extraction"
DEFAULT_CASES_PATH = EVALS_DIR / "v1" / "cases.jsonl"
DEFAULT_PLAYERS_PATH = EVALS_DIR / "v1" / "players-2026-27.json"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"
THRESHOLD_LABELS = {
    "f1": "f1 >= 0.85",
    "linking_accuracy": "linking accuracy >= 0.95",
    "false_alarm_rate": "false alarm rate <= 0.05",
    "monthly_cost": "monthly cost <= 5 PLN",
    "no_errored_cases": "no errored case",
}
RUN_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


@app.command(help="Evaluate a model on a split of the evaluation set.")
def evaluate(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split")],
    model: str | None = typer.Option(None, "--model"),
    run_name: str | None = typer.Option(None, "--run-name"),
    posts_per_month: float = typer.Option(1050.0, "--posts-per-month"),
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    players_path: Annotated[Path, typer.Option("--players")] = DEFAULT_PLAYERS_PATH,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
) -> None:
    deps = get_deps(ctx)

    selected = [case for case in load_cases(cases_path) if case.split == split]
    unreviewed = [case for case in selected if not case.reviewed]
    if unreviewed:
        raise fail(f"{len(unreviewed)} cases of the {split} split are not reviewed")
    if not selected:
        raise fail(f"the {split} split has no cases")

    usd_pln_rate = deps.settings.usd_pln_rate
    if usd_pln_rate is None:
        raise fail("USD_PLN_RATE must be set")
    if run_name is not None and not RUN_NAME_PATTERN.fullmatch(run_name):
        raise fail("--run-name may hold only letters, digits, '.', '_' and '-'")

    try:
        spec = deps.build_spec(model, fallback=False)
    except CollectorError as exc:
        raise fail(str(exc)) from None

    name = run_name or _default_run_name(split, spec, deps.clock.now())
    players, teams = load_snapshot(players_path)
    player_aliases, team_aliases = load_aliases()
    index = PlayerIndex(players, teams, player_aliases, team_aliases)
    handler = make_handler(resolve_tracing(deps.settings))
    try:
        report = run_evaluation(
            selected,
            spec,
            index,
            handler,
            run_name=name,
            split=split,
            clock=deps.clock,
            posts_per_month=posts_per_month,
            usd_pln_rate=usd_pln_rate,
        )
    finally:
        flush(handler)

    output_dir = output_dir or default_output_dir(split)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / f"{name}.json"
    result_path.write_text(json.dumps(report.to_json_dict(), ensure_ascii=False, indent=1) + "\n")

    m = report.metrics
    typer.echo(f"run: {name} ({spec.provider}:{spec.model}, {split}, {m.cases} cases)")
    typer.echo(f"errored cases: {m.errored_cases}")
    typer.echo(f"precision: {m.precision:.3f}  recall: {m.recall:.3f}  f1: {m.f1:.3f}")
    typer.echo(f"linking accuracy: {m.linking_accuracy:.3f} ({m.linking_paired} paired)")
    typer.echo(f"false alarm rate: {m.false_alarm_rate:.3f}")
    typer.echo(f"certainty accuracy: {m.certainty_accuracy:.3f}")
    typer.echo(f"latency p50/p95: {_fmt(m.latency_p50_seconds)} / {_fmt(m.latency_p95_seconds)} s")
    typer.echo(f"mean tokens in/out: {_fmt(m.mean_input_tokens)} / {_fmt(m.mean_output_tokens)}")
    typer.echo(f"mean reasoning tokens: {_fmt(m.mean_reasoning_tokens)}")
    typer.echo(f"mean cost per post: {_fmt(m.mean_cost_usd, 6)} USD")
    typer.echo(f"mean reported cost per post: {_fmt(m.mean_reported_cost_usd, 6)} USD")
    hosts = ", ".join(f"{host} {count}" for host, count in sorted(m.hosts.items())) or "n/a"
    typer.echo(f"serving hosts: {hosts}")
    typer.echo(f"projected monthly cost: {_fmt(m.projected_monthly_cost_pln, 2)} PLN")
    for name, label in THRESHOLD_LABELS.items():
        typer.echo(f"{label}: {'yes' if m.thresholds_passed[name] else 'no'}")
    typer.echo(f"passes thresholds: {'yes' if m.passes else 'no'}")
    typer.echo(f"results: {result_path}")


def default_output_dir(split: str) -> Path:
    # Dev runs are working iterations: results/dev/ is gitignored.
    return DEFAULT_RESULTS_DIR / "dev" if split == "dev" else DEFAULT_RESULTS_DIR


def _run_total(data: dict, metrics_key: str, case_key: str) -> float:
    total = data.get("metrics", {}).get(metrics_key)
    if total is not None:
        return total
    return sum(c.get(case_key) or 0.0 for c in data.get("case_results", []))


@app.command(help="Sum the cost of every evaluation run file under the results directory.")
def spend(
    results_dir: Annotated[Path, typer.Option("--results-dir")] = DEFAULT_RESULTS_DIR,
) -> None:
    # Needs no database and no LLM key, so it never builds ExtractionCliDeps.
    total = 0.0
    total_reported = 0.0
    for path in sorted(results_dir.rglob("*.json")):
        data = json.loads(path.read_text())
        cost = _run_total(data, "total_cost_usd", "cost_usd")
        reported = _run_total(data, "total_reported_cost_usd", "reported_cost_usd")
        total += cost
        total_reported += reported
        typer.echo(
            f"{data.get('run_name', path.stem)}  {data.get('split')}  {data.get('model')}  "
            f"{data.get('cases')} cases  {cost:.4f} USD  {reported:.4f} USD reported"
        )
    typer.echo(f"total cost: {total:.4f} USD (prices.toml)")
    typer.echo(f"total reported cost: {total_reported:.4f} USD (OpenRouter)")


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _default_run_name(split: str, spec: ChatModelSpec, now: datetime) -> str:
    raw = f"{split}-{spec.model}-{now:%Y%m%dT%H%M}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


DEFAULT_BASELINE_REVISION = "dc02d98"


def _read_baseline(cases_path: Path, revision: str) -> list[EvalCase]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cases_path.parent), "show", f"{revision}:./{cases_path.name}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        raise fail(f"cannot read {cases_path.name} at revision {revision}") from None
    return parse_cases(completed.stdout)


@app.command(
    "compare-labels", help="Compare the reviewed set with the pre-labelled set at a revision."
)
def compare_labels(
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    revision: Annotated[str, typer.Option("--revision")] = DEFAULT_BASELINE_REVISION,
    baseline_path: Annotated[
        Path | None, typer.Option("--baseline", help="A cases file instead of a git revision.")
    ] = None,
) -> None:
    # Needs no database and no LLM key, so it never builds ExtractionCliDeps.
    reviewed = load_cases(cases_path)
    baseline = load_cases(baseline_path) if baseline_path else _read_baseline(cases_path, revision)
    for comparison in compare_sets(reviewed, baseline):
        by_field = ", ".join(f"{k} {v}" for k, v in comparison.relabelled_by_field.items())
        typer.echo(f"split {comparison.split}")
        typer.echo(f"  cases: {comparison.cases}")
        typer.echo(f"  cases changed: {comparison.cases_changed}")
        typer.echo(
            "  events added / removed / relabelled: "
            f"{comparison.added} / {comparison.removed} / {comparison.relabelled}"
        )
        typer.echo(f"  relabelled by field: {by_field}")
        typer.echo(
            f"  ids only in the reviewed set: {', '.join(comparison.only_in_reviewed) or '-'}"
        )
        typer.echo(f"  ids only in the baseline: {', '.join(comparison.only_in_baseline) or '-'}")
        typer.echo(
            "  pre-labels precision / recall / f1: "
            f"{comparison.precision:.3f} / {comparison.recall:.3f} / {comparison.f1:.3f}"
        )


REVIEW_ACTIONS = "[a]ccept  [e]dit  [f]ind player  [s]kip  [q]uit"


@app.command(help="Review evaluation cases one by one: accept, edit or skip them.")
def review(
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    players_path: Annotated[Path, typer.Option("--players")] = DEFAULT_PLAYERS_PATH,
    split: str | None = typer.Option(None, "--split"),
    case_id: str | None = typer.Option(None, "--id", help="One case, even a reviewed one."),
) -> None:
    # Needs no database and no LLM key, so it never builds ExtractionCliDeps.
    if split not in (None, "dev", "test"):
        raise fail("--split must be dev or test")
    cases = load_cases(cases_path)
    players, teams = load_snapshot(players_path)
    directory = PlayerDirectory(players, teams, *load_aliases())

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

    counts = {"accepted": 0, "edited": 0, "skipped": 0}
    interrupted = False
    try:
        _review_cases(cases, positions, cases_path, directory, counts)
    except (typer.Abort, KeyboardInterrupt):
        interrupted = True  # every accepted or edited case is already on disk

    reviewed = sum(1 for case in cases if case.reviewed)
    typer.echo("── summary " + "─" * 61)
    typer.echo(
        f"accepted: {counts['accepted']}  edited: {counts['edited']}  skipped: {counts['skipped']}"
    )
    typer.echo(f"reviewed: {reviewed}/{len(cases)}")
    if interrupted:
        raise typer.Exit(130)


def _review_cases(
    cases: list[EvalCase],
    positions: list[int],
    cases_path: Path,
    directory: PlayerDirectory,
    counts: dict[str, int],
) -> None:
    """Walks the selected cases; `cases` is saved whole after every change, in file order."""
    for done, i in enumerate(positions):
        show = True
        while True:
            if show:
                reviewed = sum(1 for case in cases if case.reviewed)
                progress = (
                    f"{reviewed}/{len(cases)} reviewed, {len(positions) - done} left in this run"
                )
                typer.echo(render_case(cases[i], directory, progress))
            show = False
            action = typer.prompt(REVIEW_ACTIONS).strip().lower()
            if action == "a":
                cases[i] = cases[i].model_copy(update={"reviewed": True})
                write_cases(cases_path, cases)
                counts["accepted"] += 1
                break
            if action == "e":
                edited = _edit_case(cases[i])
                if edited is not None and edited != cases[i]:
                    cases[i] = edited
                    write_cases(cases_path, cases)
                    counts["edited"] += 1
                    typer.echo("saved")
                show = True
            elif action == "f":
                _find_player(directory)
            elif action == "s":
                counts["skipped"] += 1
                break
            elif action == "q":
                return
            else:
                typer.echo(f"unknown action {action!r}")


def _edit_case(case: EvalCase) -> EvalCase | None:
    try:
        command = editor_command()
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        return None
    text = case_to_json(case)
    while True:
        try:
            text = run_editor(command, text)
            return parse_edited(text, case.id)
        except (ValueError, OSError) as exc:
            typer.echo(f"not saved: {exc}")
            if typer.prompt("[r]etry edit  [c]ancel", default="r").strip().lower() != "r":
                typer.echo("edit cancelled, nothing saved")
                return None


def _find_player(directory: PlayerDirectory) -> None:
    query = typer.prompt("player name")
    club = typer.prompt("club (optional)", default="", show_default=False)
    found = directory.find(query, club.strip() or None)
    if not found:
        typer.echo("no players found")
    for player in found:
        typer.echo(render_player(player, directory))


def main() -> None:
    app(prog_name="python -m app.extraction")
