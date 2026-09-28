import json
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
from app.core.settings import ExtractionSettings, load_settings
from app.db.engine import make_engine
from app.extraction.config import resolve_llm, resolve_tracing
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases
from app.extraction.flow import PROMPT_VERSION, build_flow
from app.extraction.linking import PlayerIndex, load_aliases, load_players
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
    engine: Engine
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


def get_deps(ctx: typer.Context) -> ExtractionCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _deps_from_settings() -> ExtractionCliDeps:
    settings = ExtractionSettings()
    db_settings = load_settings()
    return ExtractionCliDeps(
        engine=make_engine(db_settings.database_url),
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

    with Session(deps.engine) as session:
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
                deps.engine, runtime, post, deps.clock, stop_event, handler, record_latency=False
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
    with Session(deps.engine) as session:
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
    with Session(deps.engine) as session:
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


def main() -> None:
    app(prog_name="python -m app.extraction")
