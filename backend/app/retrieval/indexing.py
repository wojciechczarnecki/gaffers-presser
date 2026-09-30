import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock
from app.llm.pricing import Price, compute_cost
from app.llm.retry import with_retries
from app.llm.tracing import TracingConfig
from app.retrieval.embedder import Embedder, EmbeddingResult
from app.retrieval.store import PostToEmbed, posts_missing, save_embedded, save_failed
from app.retrieval.tracing import RetrievalTracer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexingRuntime:
    model: str
    make_embedder: Callable[[], Embedder]
    prices: dict[str, Price]
    tracing: TracingConfig | None
    clock: Clock | None = None


@dataclass(frozen=True)
class IndexOutcome:
    status: str  # embedded | failed
    cost_usd: float | None


@dataclass(frozen=True)
class IndexSummary:
    embedded: int
    failed: int
    cost_usd: float | None


def traced_embed(
    embedder: Embedder,
    texts: Sequence[str],
    tracer: RetrievalTracer,
    prices: dict[str, Price],
) -> tuple[EmbeddingResult, float | None]:
    try:
        result = embedder.embed(texts)
    except Exception as exc:
        _trace_safely(
            tracer,
            model=embedder.model,
            texts=texts,
            input_tokens=None,
            cost_usd=None,
            error_class=type(exc).__name__,
        )
        raise
    cost = compute_cost(embedder.model, result.input_tokens, None, prices)
    _trace_safely(
        tracer, model=embedder.model, texts=texts, input_tokens=result.input_tokens, cost_usd=cost
    )
    return result, cost


def _trace_safely(tracer: RetrievalTracer, **kwargs) -> None:
    # A paid embedding must never be lost, or retried, because tracing it failed.
    try:
        tracer.embedding(**kwargs)
    except Exception as exc:
        logger.error("retrieval tracing failed: %s", type(exc).__name__)


def embed_post(
    engine: Engine,
    runtime: IndexingRuntime,
    embedder: Embedder,
    post: PostToEmbed,
    tracer: RetrievalTracer,
    clock: Clock,
    stop_event: threading.Event,
    record_latency: bool,
) -> IndexOutcome | None:
    retry = with_retries(
        lambda: traced_embed(embedder, [post.text], tracer, runtime.prices),
        clock,
        stop_event,
        what="embedding",
    )
    if retry.stopped:
        return None
    now = clock.now()
    if retry.result is not None:
        result, cost = retry.result  # type: ignore[misc]
        latency = (now - post.first_fetched_at).total_seconds() if record_latency else None
        with Session(engine) as session, session.begin():
            save_embedded(
                session,
                x_id=post.x_id,
                model=runtime.model,
                vector=result.vectors[0],
                input_tokens=result.input_tokens,
                cost_usd=cost,
                latency_seconds=latency,
                attempts=retry.attempts,
                now=now,
            )
        return IndexOutcome("embedded", cost)
    error_class = type(retry.error).__name__ if retry.error is not None else "UnknownError"
    logger.error("embedding of a post failed: %s", error_class)
    with Session(engine) as session, session.begin():
        save_failed(
            session,
            x_id=post.x_id,
            model=runtime.model,
            error_class=error_class,
            attempts=retry.attempts,
            now=now,
        )
    return IndexOutcome("failed", None)


def index_missing(
    engine: Engine,
    runtime: IndexingRuntime,
    embedder: Embedder,
    tracer: RetrievalTracer,
    clock: Clock,
    stop_event: threading.Event,
    record_latency: bool = False,
) -> IndexSummary:
    with Session(engine) as session:
        posts = posts_missing(session, runtime.model)
    embedded = failed = 0
    costs: list[float] = []
    for post in posts:
        outcome = embed_post(
            engine, runtime, embedder, post, tracer, clock, stop_event, record_latency
        )
        if outcome is None:
            break
        if outcome.status == "embedded":
            embedded += 1
        else:
            failed += 1
        if outcome.cost_usd is not None:
            costs.append(outcome.cost_usd)
    return IndexSummary(embedded, failed, sum(costs) if costs else None)
