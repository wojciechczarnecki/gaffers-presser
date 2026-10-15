import threading
from collections.abc import Callable, Collection, Mapping, Sequence

from sqlalchemy import Engine

from app.core.clock import Clock
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.retrieval.evaluation.dataset import EvalSet, EvaluationError, PlayerQuery
from app.retrieval.evaluation.labels import AnyQuery
from app.retrieval.evaluation.metrics import MODES, TOP
from app.retrieval.evaluation.player_metrics import judge_cut
from app.retrieval.evaluation.runner import (
    DEFAULT_MAX_COST_USD,
    LEG_DEPTH,
    Estimate,
    SearchTracer,
    check_cost,
    estimate_embedding,
    player_texts,
    prepare_model,
    ranked_search,
)
from app.retrieval.evaluation.schema import load_eval_corpus
from app.retrieval.search import SearchFilters
from app.retrieval.tracing import RetrievalTracer

# model, mode, variant ("" for a free query)
ConfigKey = tuple[str, str, str]


def pool_ids(query: AnyQuery, rankings: Mapping[ConfigKey, Sequence[int]], depth: int) -> list[int]:
    pooled: dict[int, None] = {}
    claimed = {c.x_id for c in query.claimed} if isinstance(query, PlayerQuery) else set()
    for ranked in rankings.values():
        if isinstance(query, PlayerQuery):
            shown = judge_cut(ranked, claimed, depth)
        else:
            shown = list(ranked[:TOP])
        pooled.update(dict.fromkeys(shown))
    return list(pooled)


def new_posts(query: AnyQuery, pooled: Collection[int], for_judge: str | None = None) -> list[int]:
    if for_judge is None:
        seen = {j.x_id for j in query.judgements}
    else:
        seen = {j.x_id for j in query.judgements if j.labelled_by == for_judge}
    return [x_id for x_id in pooled if x_id not in seen]


def pool_set(
    engine: Engine,
    eval_set: EvalSet,
    queries: Sequence[AnyQuery],
    embedders: Sequence[Embedder],
    *,
    depths: Sequence[int],
    k: int,
    prices: dict[str, Price],
    tracer: RetrievalTracer,
    clock: Clock,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
    on_estimate: Callable[[str, Estimate], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, list[int]]:
    if not embedders:
        raise EvaluationError("pooling needs at least one embedding model")
    stop_event = stop_event or threading.Event()
    corpus_texts = [post.text for post in eval_set.corpus]
    total = 0.0
    for embedder in embedders:
        estimate = estimate_embedding(corpus_texts, queries, embedder.model, prices)
        if on_estimate is not None:
            on_estimate(embedder.model, estimate)
        total += estimate.cost_usd
    check_cost(Estimate(0, total), max_cost_usd)

    list_handles = eval_set.manifest.list_handles if eval_set.manifest else None
    eval_engine = load_eval_corpus(engine, eval_set.corpus, list_handles)
    deepest = max(depths)
    search_tracer = SearchTracer(tracer)
    rankings: dict[str, dict[ConfigKey, list[int]]] = {query.id: {} for query in queries}
    for embedder in embedders:
        cached = prepare_model(eval_engine, embedder, queries, tracer, prices, clock, stop_event)
        for query in queries:
            if isinstance(query, PlayerQuery):
                filters = SearchFilters(
                    since=query.window_start, until=query.window_end, sources_only=True
                )
                texts = list(player_texts(query).items())
                limit, depth = deepest, max(LEG_DEPTH, deepest)
            else:
                filters = SearchFilters()
                texts = [("", query.text)]
                limit, depth = TOP, LEG_DEPTH
            for variant, text in texts:
                for mode in MODES:
                    rankings[query.id][(embedder.model, mode, variant)] = ranked_search(
                        eval_engine,
                        cached,
                        text,
                        mode,
                        limit=limit,
                        depth=depth,
                        filters=filters,
                        who=f"{query.id}/{variant}" if variant else query.id,
                        k=k,
                        tracer=search_tracer,
                        prices=prices,
                    )
    tracer.flush()
    return {query.id: pool_ids(query, rankings[query.id], deepest) for query in queries}
