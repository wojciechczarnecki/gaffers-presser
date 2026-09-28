import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import CollectorError, ConfigError
from app.core.settings import ExtractionSettings, load_settings
from app.db.engine import make_engine
from app.extraction.config import resolve_llm, resolve_tracing
from app.extraction.providers import ChatModelSpec, build_chat_model
from app.extraction.service import ExtractionRuntime, extract_post
from app.extraction.store import posts_for_reextract
from app.extraction.tracing import flush, make_handler
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


def main() -> None:
    app(prog_name="python -m app.extraction")
