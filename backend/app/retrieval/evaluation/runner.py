import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from app.core.clock import Clock
from app.core.errors import CollectorError
from app.llm.pricing import Price
from app.llm.retry import with_retries
from app.retrieval.embedder import Embedder, EmbeddingResult
from app.retrieval.evaluation.dataset import CorpusPost, Query
from app.retrieval.evaluation.metrics import (
    MODES,
    SLICES,
    Aggregate,
    QueryOutcome,
    aggregate,
)
from app.retrieval.evaluation.schema import load_eval_corpus
from app.retrieval.indexing import IndexingRuntime, index_missing, traced_embed
from app.retrieval.search import SearchError, search
from app.retrieval.tracing import RetrievalTracer


class EvaluationError(CollectorError):
    pass


class _SearchTracer:
    # Query embeddings are traced once by the runner; searches reuse them from the cache.
    def __init__(self, inner: RetrievalTracer) -> None:
        self._inner = inner

    def embedding(self, **_: Any) -> None:
        return None

    def search(self, **kwargs: Any) -> None:
        self._inner.search(**kwargs)

    def flush(self) -> None:
        self._inner.flush()


class _CachedQueryEmbedder:
    def __init__(self, inner: Embedder, cache: dict[str, list[float]]) -> None:
        self.model = inner.model
        self._inner = inner
        self._cache = cache

    def embed(
        self, texts: Sequence[str], *, timeout_seconds: float | None = None
    ) -> EmbeddingResult:
        if all(text in self._cache for text in texts):
            return EmbeddingResult(vectors=[self._cache[text] for text in texts], input_tokens=None)
        if timeout_seconds is None:
            return self._inner.embed(texts)
        return self._inner.embed(texts, timeout_seconds=timeout_seconds)


@dataclass(frozen=True)
class EvaluationResult:
    data: dict[str, Any]
    aggregate: Aggregate


def default_run_name(split: str, model: str) -> str:
    return f"{split}-{model.replace('/', '-')}"


def relevant_ids(query: Query, include_unreviewed: bool) -> tuple[frozenset[int], int]:
    considered = [j for j in query.judgements if j.reviewed or include_unreviewed]
    return frozenset(j.x_id for j in considered if j.relevant), len(considered)


def _rank_of(ranked: Sequence[int], x_id: int) -> int | None:
    return ranked.index(x_id) + 1 if x_id in ranked else None


def _metrics_json(result: Aggregate) -> dict[str, Any]:
    return {
        "queries_without_relevant": result.queries_without_relevant,
        "modes": {
            mode: {
                name: {
                    "recall_at_5": result.modes[mode][name].recall_at_5,
                    "recall_at_10": result.modes[mode][name].recall_at_10,
                    "mrr": result.modes[mode][name].mrr,
                    "n": result.modes[mode][name].n,
                }
                for name in SLICES
            }
            for mode in MODES
        },
    }


def run_evaluation(
    engine: Engine,
    corpus: Sequence[CorpusPost],
    queries: Sequence[Query],
    embedder: Embedder,
    tracer: RetrievalTracer,
    prices: dict[str, Price],
    clock: Clock,
    *,
    split: str,
    include_unreviewed: bool = False,
    k: int = 60,
    depth: int = 50,
    limit: int = 10,
    run_name: str,
    stop_event: threading.Event | None = None,
) -> EvaluationResult:
    stop_event = stop_event or threading.Event()
    chosen = [query for query in queries if query.split == split]
    judged = {query.id: relevant_ids(query, include_unreviewed) for query in chosen}
    if not any(considered for _, considered in judged.values()):
        raise EvaluationError(
            f"no reviewed labels in the {split} split; run review or pass --include-unreviewed"
        )

    eval_engine = load_eval_corpus(engine, corpus)
    runtime = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=prices,
        tracing=None,
        clock=clock,
    )
    indexed = index_missing(eval_engine, runtime, embedder, tracer, clock, stop_event)
    if indexed.failed:
        raise EvaluationError(f"{indexed.failed} corpus posts could not be embedded")

    cache: dict[str, list[float]] = {}
    for query in chosen:
        outcome = with_retries(
            lambda text=query.text: traced_embed(embedder, [text], tracer, prices),
            clock,
            stop_event,
        )
        if outcome.result is None:
            name = type(outcome.error).__name__ if outcome.error else "stopped"
            raise EvaluationError(f"query {query.id} could not be embedded: {name}")
        cache[query.text] = outcome.result[0].vectors[0]  # type: ignore[index]

    cached = _CachedQueryEmbedder(embedder, cache)
    search_tracer = _SearchTracer(tracer)
    outcomes: list[QueryOutcome] = []
    per_query: list[dict[str, Any]] = []
    for query in chosen:
        relevant, _ = judged[query.id]
        ranked: dict[str, list[int]] = {}
        for mode in MODES:
            try:
                response = search(
                    eval_engine,
                    query.text,
                    mode,
                    embedder=cached,
                    limit=limit,
                    k=k,
                    depth=depth,
                    tracer=search_tracer,
                    prices=prices,
                )
            except SearchError as exc:
                raise EvaluationError(f"query {query.id}: {exc}") from None
            if response.failed_legs:
                raise EvaluationError(f"query {query.id}: the vector leg failed")
            ranked[mode] = [result.x_id for result in response.results]
        outcomes.append(
            QueryOutcome(
                id=query.id,
                language=query.language,
                origin=query.origin,
                relevant=relevant,
                ranked=ranked,
            )
        )
        per_query.append(
            {
                "id": query.id,
                "language": query.language,
                "origin": query.origin,
                "relevant": sorted(relevant),
                "modes": {
                    mode: {
                        "retrieved": ranked[mode],
                        "relevant_ranks": {
                            str(x_id): _rank_of(ranked[mode], x_id) for x_id in sorted(relevant)
                        },
                    }
                    for mode in MODES
                },
            }
        )
    tracer.flush()

    result = aggregate(outcomes)
    data = {
        "run_name": run_name,
        "embedding_model": embedder.model,
        "k": k,
        "depth": depth,
        "limit": limit,
        "split": split,
        "include_unreviewed": include_unreviewed,
        "date": datetime.now(UTC).isoformat(),
        "metrics": _metrics_json(result),
        "queries": per_query,
    }
    return EvaluationResult(data=data, aggregate=result)


def format_table(result: Aggregate) -> str:
    lines = [f"{'mode':<10}{'slice':<7}{'n':>4}{'recall@5':>10}{'recall@10':>11}{'MRR':>8}"]
    for mode in MODES:
        for name in SLICES:
            metrics = result.modes[mode][name]
            lines.append(
                f"{mode:<10}{name:<7}{metrics.n:>4}{metrics.recall_at_5:>10.3f}"
                f"{metrics.recall_at_10:>11.3f}{metrics.mrr:>8.3f}"
            )
    if result.queries_without_relevant:
        lines.append(f"queries without a relevant post: {result.queries_without_relevant}")
    return "\n".join(lines)


def write_result(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
