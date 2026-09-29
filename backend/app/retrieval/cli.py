import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

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
from app.retrieval.evaluation.dataset import DEFAULT_CORPUS_PATH, export_corpus, write_corpus
from app.retrieval.indexing import IndexingRuntime, index_missing
from app.retrieval.search import Mode, SearchError, SearchFilters, search
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


WARSAW = ZoneInfo("Europe/Warsaw")
KEY_HINT = "OPENROUTER_API_KEY is not set; use --mode fulltext or set the key"


def parse_warsaw(value: str, option: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise fail(f"{option} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(UTC)


def _warsaw(moment: datetime) -> str:
    return moment.astimezone(WARSAW).strftime("%Y-%m-%d %H:%M")


def _rank(rank: int | None) -> str:
    return "-" if rank is None else str(rank)


@app.command(name="search", help="Search posts by full text, vector similarity or both.")
def search_command(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="The query text.")],
    mode: Annotated[Mode, typer.Option("--mode", help="fulltext, vector or hybrid.")] = "hybrid",
    limit: Annotated[int, typer.Option("--limit", min=1)] = 10,
    since: Annotated[str | None, typer.Option("--since", help="Europe/Warsaw time.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="Europe/Warsaw time.")] = None,
    exclude_reposts: Annotated[bool, typer.Option("--exclude-reposts")] = False,
    exclude_replies: Annotated[bool, typer.Option("--exclude-replies")] = False,
    model: Annotated[str | None, typer.Option("--model", help="Embedding model ID.")] = None,
    k: Annotated[int, typer.Option("--k", min=0)] = 60,
    depth: Annotated[int, typer.Option("--depth", min=1)] = 50,
) -> None:
    deps = get_deps(ctx)
    filters = SearchFilters(
        since=parse_warsaw(since, "--since") if since is not None else None,
        until=parse_warsaw(until, "--until") if until is not None else None,
        exclude_reposts=exclude_reposts,
        exclude_replies=exclude_replies,
    )
    embedder = None
    try:
        prices = deps.prices if deps.prices is not None else load_prices()
        if mode != "fulltext":
            embedder = deps.make_embedder(model)
    except CollectorError as exc:
        message = str(exc)
        raise fail(KEY_HINT if "OPENROUTER_API_KEY is not set" in message else message) from None
    engine = db_engine(deps)
    tracer = deps.make_tracer()
    try:
        response = search(
            engine,
            query,
            mode,
            embedder=embedder,
            filters=filters,
            limit=limit,
            k=k,
            depth=depth,
            tracer=tracer,
            prices=prices,
        )
    except SearchError as exc:
        raise fail(str(exc)) from None
    finally:
        tracer.flush()

    window = ""
    if filters.since is not None:
        window += f"  since {_warsaw(filters.since)}"
    if filters.until is not None:
        window += f"  until {_warsaw(filters.until)}"
    typer.echo(f"mode: {response.mode}  model: {response.model or '-'}{window} (Europe/Warsaw)")
    if response.failed_legs:
        typer.echo("vector leg failed — full-text only")
    if not response.results:
        typer.echo("no results")
    for number, result in enumerate(response.results, start=1):
        typer.echo(
            f"{number}. {result.score:.4f}  fts={_rank(result.ranks['fulltext'])}"
            f"  vec={_rank(result.ranks['vector'])}  @{result.author_handle}"
            f"  {_warsaw(result.created_at)}  {result.x_id}"
        )
        typer.echo(f"    {' '.join(result.text.split())}")


@app.command(name="export-corpus", help="Export every stored post (public fields only).")
def export_corpus_command(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output")] = DEFAULT_CORPUS_PATH,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    deps = get_deps(ctx)
    if output.exists() and not force:
        raise fail(f"{output} already exists; pass --force to overwrite it")
    posts = export_corpus(db_engine(deps))
    output.parent.mkdir(parents=True, exist_ok=True)
    write_corpus(output, posts)
    typer.echo(f"exported {len(posts)} posts to {output}")


def main() -> None:
    app(prog_name="python -m app.retrieval")
