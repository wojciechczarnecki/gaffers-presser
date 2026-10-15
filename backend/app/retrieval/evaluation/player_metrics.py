from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from app.retrieval.evaluation.dataset import PlayerQuery
from app.retrieval.variants import has_initial

JUDGE_CUT = 10
DEFAULT_DEPTHS = (20, 30, 50)
RAW_TOP = 10


def judge_cut(ranked: Sequence[int], claimed: Collection[int], depth: int) -> list[int]:
    return [x_id for x_id in ranked[:depth] if x_id not in claimed][:JUDGE_CUT]


@dataclass(frozen=True)
class QueryNumbers:
    cut: list[int]
    recall_at_cut: float | None
    recall_at_10: float | None
    empty_cut_with_miss: bool
    unjudged: int


def query_numbers(
    ranked: Sequence[int], claimed: Collection[int], labels: Mapping[int, bool], depth: int
) -> QueryNumbers:
    relevant = {x_id for x_id, value in labels.items() if value}
    unclaimed = relevant - set(claimed)
    cut = judge_cut(ranked, claimed, depth)
    return QueryNumbers(
        cut=cut,
        recall_at_cut=len(set(cut) & unclaimed) / len(unclaimed) if unclaimed else None,
        recall_at_10=len(set(ranked[:RAW_TOP]) & relevant) / len(relevant) if relevant else None,
        empty_cut_with_miss=not cut and bool(unclaimed),
        unjudged=sum(1 for x_id in cut if x_id not in labels),
    )


@dataclass(frozen=True)
class PlayerAggregate:
    queries: int
    recall_at_cut: float | None
    recall_at_10: float | None
    empty_cuts_with_miss: int
    unjudged_share: float | None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate_player(numbers: Sequence[QueryNumbers]) -> PlayerAggregate:
    cut_total = sum(len(n.cut) for n in numbers)
    return PlayerAggregate(
        queries=len(numbers),
        recall_at_cut=_mean([n.recall_at_cut for n in numbers if n.recall_at_cut is not None]),
        recall_at_10=_mean([n.recall_at_10 for n in numbers if n.recall_at_10 is not None]),
        empty_cuts_with_miss=sum(n.empty_cut_with_miss for n in numbers),
        unjudged_share=sum(n.unjudged for n in numbers) / cut_total if cut_total else None,
    )


@dataclass(frozen=True)
class PlayerCounts:
    queries: int
    no_unclaimed_relevant: int
    no_relevant: int
    with_initial: int
    stale_claims: int
    stale_anchors: int


def player_counts(
    queries: Sequence[PlayerQuery], labels_by_query: Mapping[str, Mapping[int, bool]]
) -> PlayerCounts:
    no_unclaimed = no_relevant = stale_anchors = 0
    for query in queries:
        labels = labels_by_query.get(query.id, {})
        relevant = {x_id for x_id, value in labels.items() if value}
        no_relevant += not relevant
        no_unclaimed += not (relevant - {c.x_id for c in query.claimed})
        if query.claimed:
            anchor = max(query.claimed, key=lambda c: (c.created_at, c.x_id))
            stale_anchors += anchor.stale
    return PlayerCounts(
        queries=len(queries),
        no_unclaimed_relevant=no_unclaimed,
        no_relevant=no_relevant,
        with_initial=sum(has_initial(q.player.web_name) for q in queries),
        stale_claims=sum(c.stale for q in queries for c in q.claimed),
        stale_anchors=stale_anchors,
    )
