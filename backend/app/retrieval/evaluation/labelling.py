import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from app.content import load_prompt
from app.core.clock import Clock
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.retrieval.evaluation.dataset import CorpusPost, Judgement, Query, write_queries
from app.retrieval.evaluation.llm import RelevanceLabel, StructuredCaller
from app.retrieval.evaluation.runner import EvaluationError
from app.retrieval.evaluation.schema import load_eval_corpus
from app.retrieval.indexing import IndexingRuntime, index_missing
from app.retrieval.search import search
from app.retrieval.tracing import RetrievalTracer

POOL_DEPTH = 10
POOLED_MODES = ("fulltext", "vector", "hybrid")


class CostTracer:
    def __init__(self, inner: RetrievalTracer) -> None:
        self._inner = inner
        self.embedding_cost_usd: float | None = None

    def embedding(self, **kwargs: Any) -> None:
        cost = kwargs.get("cost_usd")
        if cost is not None:
            self.embedding_cost_usd = (self.embedding_cost_usd or 0.0) + cost
        self._inner.embedding(**kwargs)

    def search(self, **kwargs: Any) -> None:
        self._inner.search(**kwargs)

    def flush(self) -> None:
        self._inner.flush()


@dataclass(frozen=True)
class PrelabelSummary:
    queries_labelled: int
    candidates: int
    relevant: int
    failures: int
    cost_usd: float | None


def pool(
    engine: Engine,
    query: Query,
    embedder: Embedder,
    tracer: RetrievalTracer,
    prices: dict[str, Price] | None = None,
) -> list[int]:
    pooled: list[int] = []
    for mode in POOLED_MODES:
        response = search(
            engine,
            query.text,
            mode,
            embedder=embedder,
            limit=POOL_DEPTH,
            tracer=tracer,
            prices=prices,
        )
        for result in response.results:
            if result.x_id not in pooled:
                pooled.append(result.x_id)
    if query.origin == "post" and query.source_x_id is not None:
        if query.source_x_id not in pooled:
            pooled.append(query.source_x_id)
    return pooled


def label(caller: StructuredCaller, query_text: str, post: CorpusPost) -> bool:
    prompt = load_prompt("retrieval_relevance")
    human = f"Query: {query_text}\n\nPost by @{post.author_handle}:\n{post.text}"
    return caller.call(RelevanceLabel, prompt.text, human).relevant


def prelabel(
    engine: Engine,
    corpus: Sequence[CorpusPost],
    queries: list[Query],
    queries_path: Path,
    embedder: Embedder,
    caller: StructuredCaller,
    prices: dict[str, Price],
    tracer: RetrievalTracer,
    clock: Clock,
    stop_event: threading.Event | None = None,
) -> PrelabelSummary:
    stop_event = stop_event or threading.Event()
    counting = CostTracer(tracer)
    eval_engine = load_eval_corpus(engine, corpus)
    runtime = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=prices,
        tracing=None,
        clock=clock,
    )
    indexed = index_missing(eval_engine, runtime, embedder, counting, clock, stop_event)
    if indexed.failed:
        raise EvaluationError(f"{indexed.failed} corpus posts could not be embedded")

    by_id = {post.x_id: post for post in corpus}
    labelled = candidates = relevant = failures = 0
    for index, query in enumerate(queries):
        if query.judgements:
            continue
        judgements = []
        failed = False
        for x_id in pool(eval_engine, query, embedder, counting, prices):
            candidates += 1
            try:
                is_relevant = label(caller, query.text, by_id[x_id])
            except Exception:
                failures += 1
                failed = True
                continue
            relevant += int(is_relevant)
            judgements.append(
                Judgement(x_id=x_id, relevant=is_relevant, reviewed=False, labelled_by=caller.model)
            )
        if failed:
            # Left without judgements, so that a re-run labels the whole pool again.
            continue
        queries[index] = query.model_copy(update={"judgements": judgements})
        write_queries(queries_path, queries)
        labelled += 1

    # the counting tracer has seen the corpus indexing and every query embedding
    costs = [c for c in (counting.embedding_cost_usd, caller.cost_usd) if c is not None]
    return PrelabelSummary(
        queries_labelled=labelled,
        candidates=candidates,
        relevant=relevant,
        failures=failures,
        cost_usd=sum(costs) if costs else None,
    )
