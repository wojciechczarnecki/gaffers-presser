from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

MODES = ("fulltext", "vector", "hybrid")
SLICES = ("all", "en", "pl")
TOP = 10


def recall_at_k(ranked: Sequence[int], relevant: Collection[int], k: int) -> float:
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank(ranked: Sequence[int], relevant: Collection[int]) -> float:
    for rank, x_id in enumerate(ranked, start=1):
        if x_id in relevant:
            return 1.0 / rank
    return 0.0


@dataclass(frozen=True)
class QueryOutcome:
    id: str
    language: str
    origin: str
    relevant: frozenset[int]
    ranked: dict[str, list[int]]
    labelled: frozenset[int] = frozenset()


@dataclass(frozen=True)
class SliceMetrics:
    recall_at_5: float
    recall_at_10: float
    mrr: float
    n: int
    unjudged_share: float | None = None


@dataclass(frozen=True)
class Aggregate:
    modes: dict[str, dict[str, SliceMetrics]]
    queries_without_relevant: int = field(default=0)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _unjudged_share(outcomes: Sequence[QueryOutcome], mode: str) -> float | None:
    shown = [(o.ranked.get(mode, [])[:TOP], o.labelled) for o in outcomes]
    total = sum(len(ranked) for ranked, _ in shown)
    if not total:
        return None
    return sum(1 for ranked, labelled in shown for x_id in ranked if x_id not in labelled) / total


def aggregate(outcomes: Sequence[QueryOutcome]) -> Aggregate:
    evaluable = [outcome for outcome in outcomes if outcome.relevant]
    modes: dict[str, dict[str, SliceMetrics]] = {}
    for mode in MODES:
        modes[mode] = {}
        for name in SLICES:
            chosen = [o for o in evaluable if name == "all" or o.language == name]
            ranked = [(o.ranked.get(mode, []), o.relevant) for o in chosen]
            modes[mode][name] = SliceMetrics(
                recall_at_5=_mean([recall_at_k(r, rel, 5) for r, rel in ranked]),
                recall_at_10=_mean([recall_at_k(r, rel, 10) for r, rel in ranked]),
                mrr=_mean([reciprocal_rank(r, rel) for r, rel in ranked]),
                n=len(chosen),
                unjudged_share=_unjudged_share(
                    [o for o in outcomes if name == "all" or o.language == name], mode
                ),
            )
    return Aggregate(modes=modes, queries_without_relevant=len(outcomes) - len(evaluable))
