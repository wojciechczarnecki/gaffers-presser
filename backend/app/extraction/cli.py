import json
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import CollectorError, ConfigError
from app.core.settings import ExtractionSettings, load_settings
from app.db.engine import make_engine
from app.extraction.config import resolve_llm, resolve_tracing
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases
from app.extraction.evaluation.runner import run_evaluation
from app.extraction.flow import PROMPT_VERSION, build_flow
from app.extraction.linking import PlayerIndex, load_aliases, load_players, load_snapshot
from app.extraction.providers import ChatModelSpec, build_chat_model
from app.extraction.service import ExtractionRuntime, extract_post, run_with_retries
from app.extraction.store import posts_for_reextract
from app.extraction.tracing import flush, make_handler, run_config
from app.tweets.loop import Clock
from app.worker.loop import SystemClock

app = typer.Typer(add_completion=False, no_args_is_help=True, help="The extraction toolkit.")

BuildSpec = Callable[[str | None, str | None], ChatModelSpec]


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
    def build_spec(provider: str | None, model: str | None) -> ChatModelSpec:
        config = resolve_llm(settings, provider, model)
        if config is None:
            raise ConfigError("LLM_PROVIDER must be set")
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
    except ValidationError:
        return None  # `evaluate` needs no database; the other commands say so through db_engine


def _deps_from_settings() -> ExtractionCliDeps:
    settings = ExtractionSettings()
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


@app.command(help="Re-extract posts, optionally with another provider/model.")
def reextract(
    ctx: typer.Context,
    x_id: int | None = typer.Option(None, "--x-id"),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    failed: bool = typer.Option(False, "--failed"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
) -> None:
    deps = get_deps(ctx)

    selectors = [x_id is not None, since is not None or until is not None, failed]
    if sum(1 for selected in selectors if selected) != 1:
        raise fail("exactly one of --x-id, --since/--until, --failed must be given")
    if (since is not None) != (until is not None):
        raise fail("--since and --until must be given together")

    try:
        spec = deps.build_spec(provider, model)
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
        provider=spec.provider, model=spec.model, make_spec=lambda: spec, tracing=tracing
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
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    eval_set: Annotated[Path | None, typer.Option("--eval-set")] = None,
) -> None:
    deps = get_deps(ctx)
    try:
        spec = deps.build_spec(provider, model)
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


@app.command(help="Evaluate a provider and model on a split of the evaluation set.")
def evaluate(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split")],
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    run_name: str | None = typer.Option(None, "--run-name"),
    posts_per_month: float = typer.Option(1050.0, "--posts-per-month"),
    cases_path: Annotated[Path, typer.Option("--cases")] = DEFAULT_CASES_PATH,
    players_path: Annotated[Path, typer.Option("--players")] = DEFAULT_PLAYERS_PATH,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = DEFAULT_RESULTS_DIR,
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

    try:
        spec = deps.build_spec(provider, model)
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
    typer.echo(f"mean cost per post: {_fmt(m.mean_cost_usd, 6)} USD")
    typer.echo(f"projected monthly cost: {_fmt(m.projected_monthly_cost_pln, 2)} PLN")
    typer.echo(f"passes thresholds: {'yes' if m.passes else 'no'}")
    typer.echo(f"results: {result_path}")


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _default_run_name(split: str, spec: ChatModelSpec, now: datetime) -> str:
    raw = f"{split}-{spec.provider}-{spec.model}-{now:%Y%m%dT%H%M}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def main() -> None:
    app(prog_name="python -m app.extraction")
