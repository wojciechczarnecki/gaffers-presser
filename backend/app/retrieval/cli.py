import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from typing import Annotated

import typer
from langchain_core.language_models import BaseChatModel
from sqlalchemy import Engine

from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError, ConfigError
from app.core.settings import load_settings
from app.db.engine import make_engine
from app.llm.pricing import Price, load_prices
from app.llm.tracing import resolve_tracing
from app.retrieval.config import (
    DEFAULT_EMBEDDING_MODEL,
    RetrievalSettings,
    load_retrieval_settings,
    resolve_embedding,
)
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.indexing import IndexingRuntime, index_missing
from app.retrieval.store import embedding_status
from app.retrieval.tracing import RetrievalTracer, make_tracer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Hybrid retrieval over stored posts.",
)


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group whatever the number of commands.
    pass


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


@dataclass(frozen=True)
class RetrievalCliDeps:
    engine: Engine | None
    settings: RetrievalSettings
    make_embedder: Callable[[str | None], Embedder]
    clock: Clock
    make_tracer: Callable[[], RetrievalTracer]
    make_chat_model: Callable[[str], BaseChatModel] | None = None
    prices: dict[str, Price] | None = None


def db_engine(deps: RetrievalCliDeps) -> Engine:
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    return deps.engine


def get_deps(ctx: typer.Context) -> RetrievalCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _engine_from_env() -> Engine | None:
    try:
        return make_engine(load_settings().database_url)
    except ConfigError:
        return None  # commands that need no database run without it; the others say so


def embedder_from_settings(settings: RetrievalSettings) -> Callable[[str | None], Embedder]:
    def make(model: str | None) -> Embedder:
        config = resolve_embedding(settings, model)
        if config is None:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        return build_embedder(config)

    return make


def _deps_from_settings() -> RetrievalCliDeps:
    try:
        settings = load_retrieval_settings()
    except ConfigError as exc:
        raise fail(str(exc)) from None
    tracing = resolve_tracing(settings)
    return RetrievalCliDeps(
        engine=_engine_from_env(),
        settings=settings,
        make_embedder=embedder_from_settings(settings),
        clock=SystemClock(),
        make_tracer=lambda: make_tracer(tracing),
    )


def _cost(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.6f}"


@app.command(help="Embed every post that has no embedding for the model yet.")
def index(
    ctx: typer.Context,
    model: Annotated[str | None, typer.Option("--model", help="Embedding model ID.")] = None,
) -> None:
    deps = get_deps(ctx)
    try:
        embedder = deps.make_embedder(model)
        prices = deps.prices if deps.prices is not None else load_prices()
    except CollectorError as exc:
        raise fail(str(exc)) from None
    engine = db_engine(deps)
    runtime = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=prices,
        tracing=None,
        clock=deps.clock,
    )
    tracer = deps.make_tracer()
    try:
        summary = index_missing(
            engine, runtime, embedder, tracer, deps.clock, threading.Event(), record_latency=False
        )
    finally:
        tracer.flush()
    typer.echo(f"model: {embedder.model}")
    typer.echo(f"embedded: {summary.embedded}")
    typer.echo(f"failed: {summary.failed}")
    typer.echo(f"total cost: {_cost(summary.cost_usd)}")


@app.command(help="Show how many posts are embedded, missing or failed per model.")
def status(
    ctx: typer.Context,
    model: Annotated[str | None, typer.Option("--model", help="Embedding model ID.")] = None,
) -> None:
    deps = get_deps(ctx)
    configured = model or deps.settings.embedding_model or DEFAULT_EMBEDDING_MODEL
    state = embedding_status(db_engine(deps), [configured])
    typer.echo(f"posts: {state.posts}")
    for counts in state.models:
        typer.echo(
            f"model {counts.model}: embedded {counts.embedded}  missing {counts.missing}"
            f"  failed {counts.failed}"
        )
    if state.latest is None:
        typer.echo("latest embedding: never")
    else:
        latest = state.latest
        latency = "-" if latest.latency_seconds is None else f"{latest.latency_seconds:.1f}"
        when = latest.updated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        typer.echo(
            f"latest embedding: {when} x_id={latest.x_id} model={latest.model} latency={latency}s"
        )
    typer.echo(f"total embedding cost: {state.total_cost_usd:.6f} USD")


def main() -> None:
    app(prog_name="python -m app.retrieval")
