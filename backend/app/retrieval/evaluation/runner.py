import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from app.content import load_prompt
from app.core.clock import Clock
from app.core.eval_results import make_header, write_json_atomic
from app.core.retry import with_retries
from app.llm.models import reasoning_effort_of
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder, EmbeddingResult
from app.retrieval.evaluation.dataset import (
    EvalSet,
    EvaluationError,
    FreeQuery,
    PlayerQuery,
)
from app.retrieval.evaluation.labels import AnyQuery, label_counts, reference_labels
from app.retrieval.evaluation.metrics import (
    MODES,
    SLICES,
    Aggregate,
    QueryOutcome,
    aggregate,
)
from app.retrieval.evaluation.player_metrics import (
    DEFAULT_DEPTHS,
    PlayerAggregate,
    PlayerCounts,
    QueryNumbers,
    aggregate_player,
    player_counts,
    query_numbers,
)
from app.retrieval.evaluation.schema import load_eval_corpus
from app.retrieval.indexing import IndexingRuntime, index_missing, traced_embed
from app.retrieval.search import SearchError, SearchFilters, search
from app.retrieval.tracing import NULL_TRACER, RetrievalTracer
from app.retrieval.variants import VARIANTS, PlayerNames, Variant, variant_text

LEG_DEPTH = 50
FREE_TOP = 10
CHARS_PER_TOKEN = 4
DEFAULT_MAX_COST_USD = 1.00
RELEVANCE_PROMPTS = {"player": "retrieval_relevance_player", "free": "retrieval_relevance_free"}

__all__ = ["EvaluationError", "EvaluationResult", "run_evaluation"]


class SearchTracer:
    # Query embeddings are traced once by the runner; searches reuse them from the cache.
    def __init__(self, inner: RetrievalTracer) -> None:
        self._inner = inner

    def embedding(self, **_: Any) -> None:
        return None

    def search(self, **kwargs: Any) -> None:
        self._inner.search(**kwargs)

    def flush(self) -> None:
        self._inner.flush()


class CachedQueryEmbedder:
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
class Estimate:
    tokens: int
    cost_usd: float


@dataclass(frozen=True)
class PlayerRow:
    mode: str
    variant: str
    depth: int
    metrics: PlayerAggregate


@dataclass(frozen=True)
class EvaluationResult:
    data: dict[str, Any]
    player_rows: list[PlayerRow]
    player_counts: PlayerCounts
    free: Aggregate


def default_run_name(split: str, set_version: str, model: str) -> str:
    return f"{split}-{set_version}-{model.replace('/', '-')}"


def player_texts(query: PlayerQuery) -> dict[Variant, str]:
    names = PlayerNames(
        web_name=query.player.web_name,
        first_name=query.player.first_name,
        second_name=query.player.second_name,
        aliases=tuple(query.player.aliases),
    )
    return {variant: variant_text(names, variant) for variant in VARIANTS}


def query_texts(queries: Sequence[AnyQuery]) -> list[str]:
    texts: dict[str, None] = {}
    for query in queries:
        if isinstance(query, PlayerQuery):
            texts.update(dict.fromkeys(player_texts(query).values()))
        else:
            texts[query.text] = None
    return list(texts)


def estimate_embedding(
    corpus_texts: Sequence[str], queries: Sequence[AnyQuery], model: str, prices: dict[str, Price]
) -> Estimate:
    price = prices.get(model)
    if price is None:
        raise EvaluationError(f"{model} has no row in prices.toml")
    characters = sum(len(text) for text in corpus_texts) + sum(
        len(text) for text in query_texts(queries)
    )
    tokens = characters // CHARS_PER_TOKEN
    return Estimate(tokens, tokens / 1_000_000 * price.input_per_million)


def check_cost(estimate: Estimate, max_cost_usd: float) -> None:
    if estimate.cost_usd > max_cost_usd:
        raise EvaluationError(
            f"the embedding is estimated at ${estimate.cost_usd:.4f}, above"
            f" --max-cost-usd {max_cost_usd:g}"
        )


def leg_groups(depths: Sequence[int]) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for depth in sorted(set(depths)):
        groups.setdefault(max(LEG_DEPTH, depth), []).append(depth)
    return groups


def embed_query_texts(
    embedder: Embedder,
    texts: Sequence[str],
    names: dict[str, str],
    tracer: RetrievalTracer,
    prices: dict[str, Price],
    clock: Clock,
    stop_event: threading.Event,
) -> dict[str, list[float]]:
    cache: dict[str, list[float]] = {}
    for text in texts:
        outcome = with_retries(
            lambda text=text: traced_embed(embedder, [text], tracer, prices), clock, stop_event
        )
        if outcome.result is None:
            error = type(outcome.error).__name__ if outcome.error else "stopped"
            raise EvaluationError(f"query {names[text]} could not be embedded: {error}")
        cache[text] = outcome.result[0].vectors[0]  # type: ignore[index]
    return cache


def prepare_model(
    eval_engine: Engine,
    embedder: Embedder,
    queries: Sequence[AnyQuery],
    tracer: RetrievalTracer,
    prices: dict[str, Price],
    clock: Clock,
    stop_event: threading.Event,
) -> CachedQueryEmbedder:
    runtime = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=prices,
        tracing=None,
        clock=clock,
    )
    # The frozen corpus is re-embedded on every run, so it is indexed untraced.
    indexed = index_missing(eval_engine, runtime, embedder, NULL_TRACER, clock, stop_event)
    if indexed.failed:
        raise EvaluationError(f"{indexed.failed} corpus posts could not be embedded")
    names: dict[str, str] = {}
    for query in queries:
        for text in query_texts([query]):
            names.setdefault(text, query.id)
    cache = embed_query_texts(embedder, list(names), names, tracer, prices, clock, stop_event)
    return CachedQueryEmbedder(embedder, cache)


def ranked_search(
    eval_engine: Engine,
    embedder: Embedder,
    text: str,
    mode: str,
    *,
    limit: int,
    depth: int,
    filters: SearchFilters,
    who: str,
    k: int,
    tracer: RetrievalTracer,
    prices: dict[str, Price],
) -> list[int]:
    try:
        response = search(
            eval_engine,
            text,
            mode,  # type: ignore[arg-type]
            embedder=embedder,
            filters=filters,
            limit=limit,
            k=k,
            depth=depth,
            tracer=tracer,
            prices=prices,
        )
    except SearchError as exc:
        raise EvaluationError(f"query {who}: {exc}") from None
    if response.failed_legs:
        raise EvaluationError(f"query {who}: the vector leg failed")
    return [result.x_id for result in response.results]


def _labelled(ids: Sequence[int], labels: dict[int, bool]) -> list[bool | None]:
    return [labels.get(x_id) for x_id in ids]


def _format_row(row: PlayerRow) -> dict[str, Any]:
    m = row.metrics
    return {
        "mode": row.mode,
        "variant": row.variant,
        "depth": row.depth,
        "queries": m.queries,
        "recall_at_cut": m.recall_at_cut,
        "recall_at_10": m.recall_at_10,
        "empty_cuts_with_miss": m.empty_cuts_with_miss,
        "unjudged_share": m.unjudged_share,
    }


def _free_json(result: Aggregate) -> dict[str, Any]:
    return {
        "queries_without_relevant": result.queries_without_relevant,
        "modes": {
            mode: {
                name: {
                    "recall_at_5": result.modes[mode][name].recall_at_5,
                    "recall_at_10": result.modes[mode][name].recall_at_10,
                    "mrr": result.modes[mode][name].mrr,
                    "n": result.modes[mode][name].n,
                    "unjudged_share": result.modes[mode][name].unjudged_share,
                }
                for name in SLICES
            }
            for mode in MODES
        },
    }


def run_evaluation(
    engine: Engine,
    eval_set: EvalSet,
    embedder: Embedder,
    tracer: RetrievalTracer,
    prices: dict[str, Price],
    clock: Clock,
    *,
    split: str,
    reference_judge: str,
    depths: Sequence[int] = DEFAULT_DEPTHS,
    k: int = 60,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
    on_estimate: Callable[[Estimate], None] | None = None,
    stop_event: threading.Event | None = None,
) -> EvaluationResult:
    stop_event = stop_event or threading.Event()
    chosen = [query for query in eval_set.queries if query.split == split]
    labels = {query.id: reference_labels(query, reference_judge) for query in chosen}
    if not any(labels.values()):
        raise EvaluationError(
            f"no label from {reference_judge} or the owner in the {split} split;"
            " import the judge's verdicts or pass another --reference-judge"
        )
    counts = label_counts(chosen, reference_judge)

    estimate = estimate_embedding(
        [post.text for post in eval_set.corpus], chosen, embedder.model, prices
    )
    if on_estimate is not None:
        on_estimate(estimate)
    check_cost(estimate, max_cost_usd)

    list_handles = eval_set.manifest.list_handles if eval_set.manifest else None
    eval_engine = load_eval_corpus(engine, eval_set.corpus, list_handles)
    cached = prepare_model(eval_engine, embedder, chosen, tracer, prices, clock, stop_event)
    search_tracer = SearchTracer(tracer)

    def run_search(
        text: str, mode: str, limit: int, depth: int, filters: SearchFilters, who: str
    ) -> list[int]:
        return ranked_search(
            eval_engine,
            cached,
            text,
            mode,
            limit=limit,
            depth=depth,
            filters=filters,
            who=who,
            k=k,
            tracer=search_tracer,
            prices=prices,
        )

    players = [q for q in chosen if isinstance(q, PlayerQuery)]
    frees = [q for q in chosen if isinstance(q, FreeQuery)]
    groups = leg_groups(depths)

    numbers: dict[tuple[str, str, int], list[QueryNumbers]] = {}
    details: dict[str, Any] = {}
    for query in players:
        claimed = {c.x_id for c in query.claimed}
        query_labels = labels[query.id]
        filters = SearchFilters(since=query.window_start, until=query.window_end, sources_only=True)
        configs = []
        for variant, text in player_texts(query).items():
            for mode in MODES:
                for leg_depth, group in groups.items():
                    ranked = run_search(
                        text, mode, max(group), leg_depth, filters, f"{query.id}/{variant}"
                    )
                    for depth in group:
                        item = query_numbers(ranked, claimed, query_labels, depth)
                        numbers.setdefault((mode, variant, depth), []).append(item)
                        configs.append(
                            {
                                "mode": mode,
                                "variant": variant,
                                "depth": depth,
                                "cut": item.cut,
                                "cut_labels": _labelled(item.cut, query_labels),
                                "top10": ranked[:10],
                                "top10_labels": _labelled(ranked[:10], query_labels),
                            }
                        )
        details[query.id] = {
            "kind": "player",
            "claimed": sorted(claimed),
            "relevant": sorted(x for x, value in query_labels.items() if value),
            "configs": configs,
        }

    outcomes: list[QueryOutcome] = []
    no_filters = SearchFilters()
    for query in frees:
        query_labels = labels[query.id]
        ranked_by_mode = {
            mode: run_search(query.text, mode, FREE_TOP, LEG_DEPTH, no_filters, query.id)
            for mode in MODES
        }
        outcomes.append(
            QueryOutcome(
                id=query.id,
                language=query.language,
                origin=query.origin,
                relevant=frozenset(x for x, value in query_labels.items() if value),
                ranked=ranked_by_mode,
                labelled=frozenset(query_labels),
            )
        )
        details[query.id] = {
            "kind": "free",
            "language": query.language,
            "relevant": sorted(x for x, value in query_labels.items() if value),
            "modes": {
                mode: {
                    "top10": ranked_by_mode[mode],
                    "top10_labels": _labelled(ranked_by_mode[mode], query_labels),
                }
                for mode in MODES
            },
        }
    tracer.flush()

    rows = [
        PlayerRow(mode, variant, depth, aggregate_player(items))
        for (mode, variant, depth), items in sorted(
            numbers.items(), key=lambda i: (MODES.index(i[0][0]), VARIANTS.index(i[0][1]), i[0][2])
        )
    ]
    run_counts = player_counts(players, {q.id: labels[q.id] for q in players})
    free = aggregate(outcomes)
    header = make_header(
        set_version=eval_set.version,
        model=embedder.model,
        prompt_version=None,
        reasoning_effort=reasoning_effort_of(embedder.model),
        judge=reference_judge,
        now=clock.now(),
        extra={
            "split": split,
            "k": k,
            "depths": sorted(set(depths)),
            "reference_judge": reference_judge,
            "relevance_prompts": {
                name: load_prompt(file).version for name, file in RELEVANCE_PROMPTS.items()
            },
            "owner_reviewed_labels": counts.owner_reviewed,
            "judge_only_labels": counts.judge_only,
            "judge_owner_agreement": counts.judge_owner_agreement,
            "estimated_embedding_tokens": estimate.tokens,
            "estimated_embedding_cost_usd": estimate.cost_usd,
        },
    )
    data = {
        "header": header,
        "player": {
            "counts": {
                "queries": run_counts.queries,
                "no_unclaimed_relevant": run_counts.no_unclaimed_relevant,
                "no_relevant": run_counts.no_relevant,
                "with_initial": run_counts.with_initial,
                "stale_claims": run_counts.stale_claims,
                "stale_anchors": run_counts.stale_anchors,
            },
            "rows": [_format_row(row) for row in rows],
        },
        "free": _free_json(free),
        "queries": details,
    }
    return EvaluationResult(data=data, player_rows=rows, player_counts=run_counts, free=free)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def format_player_table(rows: Sequence[PlayerRow], counts: PlayerCounts) -> str:
    lines = [
        f"{'mode':<10}{'variant':<11}{'depth':>6}{'n':>5}{'recall@cut':>12}{'recall@10':>11}"
        f"{'empty cuts':>12}{'unjudged':>10}"
    ]
    for row in rows:
        m = row.metrics
        lines.append(
            f"{row.mode:<10}{row.variant:<11}{row.depth:>6}{m.queries:>5}"
            f"{_fmt(m.recall_at_cut):>12}{_fmt(m.recall_at_10):>11}"
            f"{m.empty_cuts_with_miss:>12}{_fmt(m.unjudged_share):>10}"
        )
    lines.append(
        f"queries {counts.queries}: no unclaimed relevant post {counts.no_unclaimed_relevant},"
        f" no relevant post {counts.no_relevant}, web_name with an initial {counts.with_initial},"
        f" stale claims {counts.stale_claims}, stale anchors {counts.stale_anchors}"
    )
    return "\n".join(lines)


def format_table(result: Aggregate) -> str:
    lines = [
        f"{'mode':<10}{'slice':<7}{'n':>4}{'recall@5':>10}{'recall@10':>11}{'MRR':>8}"
        f"{'unjudged':>10}"
    ]
    for mode in MODES:
        for name in SLICES:
            metrics = result.modes[mode][name]
            lines.append(
                f"{mode:<10}{name:<7}{metrics.n:>4}{metrics.recall_at_5:>10.3f}"
                f"{metrics.recall_at_10:>11.3f}{metrics.mrr:>8.3f}"
                f"{_fmt(metrics.unjudged_share):>10}"
            )
    if result.queries_without_relevant:
        lines.append(f"queries without a relevant post: {result.queries_without_relevant}")
    return "\n".join(lines)


def write_result(path: Path, data: dict[str, Any]) -> None:
    write_json_atomic(path, data)
