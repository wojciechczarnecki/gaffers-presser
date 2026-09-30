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
from app.llm.chat import build_chat_model, single_model_config, structured_kwargs_for
from app.llm.pricing import Price, load_prices
from app.llm.structured import StructuredCaller
from app.llm.tracing import resolve_tracing
from app.retrieval.config import (
    DEFAULT_EMBEDDING_MODEL,
    RetrievalSettings,
    load_retrieval_settings,
    resolve_embedding,
)
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.evaluation.dataset import (
    DEFAULT_CORPUS_PATH,
    DEFAULT_QUERIES_PATH,
    DEFAULT_RESULTS_DIR,
    CorpusPost,
    Judgement,
    Query,
    export_corpus,
    load_corpus,
    load_queries,
    write_corpus,
    write_queries,
)
from app.retrieval.evaluation.labelling import prelabel
from app.retrieval.evaluation.llm import DEFAULT_LABEL_MODEL
from app.retrieval.evaluation.queries import (
    QueryCounts,
    build_queries,
    current_events,
    make_query_writer,
)
from app.retrieval.evaluation.runner import (
    EvaluationError,
    default_run_name,
    format_table,
    run_evaluation,
    write_result,
)
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


def chat_model_from_settings(settings: RetrievalSettings) -> Callable[[str], BaseChatModel]:
    def make(model: str) -> BaseChatModel:
        if settings.openrouter_api_key is None:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        return build_chat_model(single_model_config(settings.openrouter_api_key, model)).chat_model

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
        make_chat_model=chat_model_from_settings(settings),
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
        typer.echo(f"vector leg failed ({response.failure}) — full-text only")
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


def _chat_model(deps: RetrievalCliDeps, model: str) -> BaseChatModel:
    if deps.make_chat_model is None:
        raise fail("OPENROUTER_API_KEY is not set")
    try:
        return deps.make_chat_model(model)
    except CollectorError as exc:
        raise fail(str(exc)) from None


@app.command(name="build-queries", help="Build the evaluation queries from the corpus.")
def build_queries_command(
    ctx: typer.Context,
    corpus: Annotated[Path, typer.Option("--corpus")] = DEFAULT_CORPUS_PATH,
    output: Annotated[Path, typer.Option("--output")] = DEFAULT_QUERIES_PATH,
    event_en: Annotated[int, typer.Option("--event-en", min=0)] = QueryCounts.event_en,
    event_pl: Annotated[int, typer.Option("--event-pl", min=0)] = QueryCounts.event_pl,
    post_en: Annotated[int, typer.Option("--post-en", min=0)] = QueryCounts.post_en,
    post_pl: Annotated[int, typer.Option("--post-pl", min=0)] = QueryCounts.post_pl,
    seed: Annotated[int, typer.Option("--seed")] = 6,
    model: Annotated[str | None, typer.Option("--model", help="Chat model ID.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    deps = get_deps(ctx)
    if output.exists() and not force:
        raise fail(f"{output} already exists; pass --force to overwrite it")
    chat_model_id = model or DEFAULT_LABEL_MODEL
    chat_model = _chat_model(deps, chat_model_id)
    try:
        prices = deps.prices if deps.prices is not None else load_prices()
        posts = load_corpus(corpus)
    except (CollectorError, OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    events = current_events(db_engine(deps), [post.x_id for post in posts])
    caller = StructuredCaller(
        chat_model,
        chat_model_id,
        prices,
        deps.clock,
        structured_kwargs=structured_kwargs_for(chat_model_id),
    )
    result = build_queries(
        posts,
        events,
        make_query_writer(caller),
        QueryCounts(event_en, event_pl, post_en, post_pl),
        seed,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_queries(output, result.queries)
    typer.echo(f"queries: {len(result.queries)} written to {output}")
    for origin in ("event", "post"):
        for language in ("en", "pl"):
            count = sum(1 for q in result.queries if (q.origin, q.language) == (origin, language))
            typer.echo(f"  {origin} {language}: {count}")
    typer.echo(f"skipped posts: {result.skipped_posts}")
    typer.echo(f"total cost: {_cost(caller.cost_usd)}")


@app.command(name="prelabel", help="Pool candidates for every query and pre-label them.")
def prelabel_command(
    ctx: typer.Context,
    queries: Annotated[Path, typer.Option("--queries")] = DEFAULT_QUERIES_PATH,
    corpus: Annotated[Path, typer.Option("--corpus")] = DEFAULT_CORPUS_PATH,
    model: Annotated[str | None, typer.Option("--model", help="Chat model ID.")] = None,
    embedding_model: Annotated[
        str | None, typer.Option("--embedding-model", help="Embedding model ID.")
    ] = None,
) -> None:
    deps = get_deps(ctx)
    chat_model_id = model or DEFAULT_LABEL_MODEL
    chat_model = _chat_model(deps, chat_model_id)
    try:
        embedder = deps.make_embedder(embedding_model)
        prices = deps.prices if deps.prices is not None else load_prices()
        posts = load_corpus(corpus)
        query_set = load_queries(queries)
    except CollectorError as exc:
        raise fail(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    caller = StructuredCaller(
        chat_model,
        chat_model_id,
        prices,
        deps.clock,
        structured_kwargs=structured_kwargs_for(chat_model_id),
    )
    tracer = deps.make_tracer()
    try:
        summary = prelabel(
            db_engine(deps),
            posts,
            query_set,
            queries,
            embedder,
            caller,
            prices,
            tracer,
            deps.clock,
        )
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    finally:
        tracer.flush()
    typer.echo(f"queries labelled: {summary.queries_labelled}")
    typer.echo(f"candidates: {summary.candidates}")
    typer.echo(f"relevant: {summary.relevant}")
    typer.echo(f"label failures: {summary.failures}")
    if summary.failures:
        typer.echo("queries with a label failure were left unlabelled; run prelabel again")
    typer.echo(f"total cost: {_cost(summary.cost_usd)}")


@app.command(name="evaluate", help="Report recall@5, recall@10 and MRR per search mode.")
def evaluate_command(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split", help="dev or test.")],
    include_unreviewed: Annotated[bool, typer.Option("--include-unreviewed")] = False,
    embedding_model: Annotated[
        str | None, typer.Option("--embedding-model", help="Embedding model ID.")
    ] = None,
    k: Annotated[int, typer.Option("--k", min=0)] = 60,
    depth: Annotated[int, typer.Option("--depth", min=1)] = 50,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 10,
    run_name: Annotated[str | None, typer.Option("--run-name")] = None,
    queries: Annotated[Path, typer.Option("--queries")] = DEFAULT_QUERIES_PATH,
    corpus: Annotated[Path, typer.Option("--corpus")] = DEFAULT_CORPUS_PATH,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
) -> None:
    if split not in ("dev", "test"):
        raise fail("--split must be dev or test")
    deps = get_deps(ctx)
    try:
        embedder = deps.make_embedder(embedding_model)
        prices = deps.prices if deps.prices is not None else load_prices()
        posts = load_corpus(corpus)
        query_set = load_queries(queries)
    except CollectorError as exc:
        raise fail(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    name = run_name or default_run_name(split, embedder.model)
    # Dev runs are working iterations: results/dev/ is gitignored.
    directory = output_dir or (
        DEFAULT_RESULTS_DIR / "dev" if split == "dev" else DEFAULT_RESULTS_DIR
    )
    tracer = deps.make_tracer()
    try:
        result = run_evaluation(
            db_engine(deps),
            posts,
            query_set,
            embedder,
            tracer,
            prices,
            deps.clock,
            split=split,
            include_unreviewed=include_unreviewed,
            k=k,
            depth=depth,
            limit=limit,
            run_name=name,
        )
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    finally:
        tracer.flush()
    typer.echo(format_table(result.aggregate))
    path = directory / f"{name}.json"
    write_result(path, result.data)
    typer.echo(f"result written to {path}")


REVIEW_ACTIONS = "[a]ccept  [f]lip  [s]kip  [+] add post  [n]ext query  [q]uit"


def _render_judgement(
    query: Query, judgement: Judgement, post: CorpusPost | None, progress: str
) -> str:
    label = "relevant" if judgement.relevant else "not relevant"
    lines = [
        "─" * 70,
        progress,
        f"{query.id}  [{query.language}, {query.origin}]  {query.text}",
    ]
    if post is None:
        lines.append(f"post {judgement.x_id}: not in the corpus")
    else:
        lines.append(f"@{post.author_handle}  {_warsaw(post.created_at)}  {post.x_id}")
        lines.append(post.text)
    lines.append(f"{judgement.labelled_by} says: {label}")
    return "\n".join(lines)


def _save_query(queries: list[Query], index: int, judgements: list[Judgement], path: Path) -> None:
    queries[index] = queries[index].model_copy(update={"judgements": judgements})
    write_queries(path, queries)


def _review_queries(
    queries: list[Query],
    positions: list[int],
    corpus: dict[int, CorpusPost],
    path: Path,
    counts: dict[str, int],
) -> None:
    # `queries` is saved whole after every change.
    for done, position in enumerate(positions):
        pending = [j.x_id for j in queries[position].judgements if not j.reviewed]
        for x_id in pending:
            while True:
                current = queries[position].judgements
                judgement = next((j for j in current if j.x_id == x_id), None)
                if judgement is None or judgement.reviewed:
                    break  # replaced by an added post
                reviewed = sum(j.reviewed for q in queries for j in q.judgements)
                total = sum(len(q.judgements) for q in queries)
                progress = (
                    f"{reviewed}/{total} labels reviewed,"
                    f" {len(positions) - done} queries in this run"
                )
                typer.echo(
                    _render_judgement(queries[position], judgement, corpus.get(x_id), progress)
                )
                action = typer.prompt(REVIEW_ACTIONS).strip().lower()
                if action in ("a", "f"):
                    update = {"reviewed": True}
                    if action == "f":
                        update["relevant"] = not judgement.relevant
                    replaced = [
                        j.model_copy(update=update) if j is judgement else j for j in current
                    ]
                    _save_query(queries, position, replaced, path)
                    counts["accepted" if action == "a" else "flipped"] += 1
                    break
                if action == "s":
                    counts["skipped"] += 1
                    break
                if action == "+":
                    added = _add_post(queries, position, corpus, path)
                    if added:
                        counts["added"] += 1
                elif action == "n":
                    break
                elif action == "q":
                    return
                else:
                    typer.echo(f"unknown action {action!r}")
            if action == "n":
                break


def _add_post(
    queries: list[Query], position: int, corpus: dict[int, CorpusPost], path: Path
) -> bool:
    raw = typer.prompt("X ID of the relevant post").strip()
    if not raw.isdigit() or int(raw) not in corpus:
        typer.echo(f"{raw!r} is not in the corpus")
        return False
    added = Judgement(x_id=int(raw), relevant=True, reviewed=True, labelled_by="owner")
    kept = [j for j in queries[position].judgements if j.x_id != added.x_id]
    _save_query(queries, position, [*kept, added], path)
    typer.echo("saved")
    return True


@app.command(help="Review the pre-labelled judgements: accept, flip, skip or add a post.")
def review(
    queries_path: Annotated[Path, typer.Option("--queries")] = DEFAULT_QUERIES_PATH,
    corpus_path: Annotated[Path, typer.Option("--corpus")] = DEFAULT_CORPUS_PATH,
    split: Annotated[str | None, typer.Option("--split")] = None,
) -> None:
    # Needs no database and no key, so it never builds RetrievalCliDeps.
    if split not in (None, "dev", "test"):
        raise fail("--split must be dev or test")
    try:
        queries = load_queries(queries_path)
        corpus = {post.x_id: post for post in load_corpus(corpus_path)}
    except (OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    positions = [
        i
        for i, query in enumerate(queries)
        if any(not j.reviewed for j in query.judgements) and split in (None, query.split)
    ]
    if not positions:
        typer.echo("nothing to review")
        return
    counts = {"accepted": 0, "flipped": 0, "skipped": 0, "added": 0}
    interrupted = False
    try:
        _review_queries(queries, positions, corpus, queries_path, counts)
    except (typer.Abort, KeyboardInterrupt):
        interrupted = True  # every decision is already on disk
    reviewed = sum(j.reviewed for q in queries for j in q.judgements)
    typer.echo("── summary " + "─" * 61)
    typer.echo(
        f"accepted: {counts['accepted']}  flipped: {counts['flipped']}"
        f"  skipped: {counts['skipped']}  added: {counts['added']}"
    )
    typer.echo(f"reviewed: {reviewed}/{sum(len(q.judgements) for q in queries)}")
    if interrupted:
        raise typer.Exit(130)


def main() -> None:
    app(prog_name="python -m app.retrieval")
