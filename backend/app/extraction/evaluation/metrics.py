import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.extraction.evaluation.cases import ExpectedEvent
from app.extraction.linking import normalise
from app.extraction.schemas import Certainty

CERTAINTY_LEVELS: tuple[Certainty, ...] = ("confirmed", "likely", "rumour")
MIN_F1 = 0.85
MIN_LINKING_ACCURACY = 0.95
MAX_FALSE_ALARM_RATE = 0.05
MAX_MONTHLY_COST_PLN = 5.0


@dataclass(frozen=True)
class CaseResult:
    expected: Sequence[ExpectedEvent]
    predicted: Sequence[ExpectedEvent]
    latency_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error_class: str | None = None
    reasoning_tokens: int | None = None
    reported_cost_usd: float | None = None
    host: str | None = None
    answered_model: str | None = None
    generation_id: str | None = None


@dataclass(frozen=True)
class Metrics:
    cases: int
    errored_cases: int
    precision: float
    recall: float
    f1: float
    linking_accuracy: float
    linking_paired: int
    false_alarm_rate: float
    certainty_accuracy: float
    certainty_confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    latency_p50_seconds: float | None = None
    latency_p95_seconds: float | None = None
    mean_input_tokens: float | None = None
    mean_output_tokens: float | None = None
    mean_cost_usd: float | None = None
    projected_monthly_cost_pln: float | None = None
    posts_per_month: float = 0.0
    usd_pln_rate: float = 0.0
    passes: bool = False
    mean_reasoning_tokens: float | None = None
    mean_reported_cost_usd: float | None = None
    total_cost_usd: float = 0.0
    total_reported_cost_usd: float = 0.0
    hosts: dict[str, int] = field(default_factory=dict)
    answered_models: dict[str, int] = field(default_factory=dict)
    thresholds_passed: dict[str, bool] = field(default_factory=dict)


def threshold_flags(
    f1: float,
    linking_accuracy: float,
    false_alarm_rate: float,
    monthly_cost_pln: float | None,
    errored_cases: int = 0,
) -> dict[str, bool]:
    return {
        "f1": f1 >= MIN_F1,
        "linking_accuracy": linking_accuracy >= MIN_LINKING_ACCURACY,
        "false_alarm_rate": false_alarm_rate <= MAX_FALSE_ALARM_RATE,
        "monthly_cost": monthly_cost_pln is not None and monthly_cost_pln <= MAX_MONTHLY_COST_PLN,
        "no_errored_cases": errored_cases == 0,
    }


def passes(
    f1: float,
    linking_accuracy: float,
    false_alarm_rate: float,
    monthly_cost_pln: float | None,
    errored_cases: int = 0,
) -> bool:
    flags = threshold_flags(f1, linking_accuracy, false_alarm_rate, monthly_cost_pln, errored_cases)
    return all(flags.values())


def _key(event: ExpectedEvent) -> tuple[str, int | str]:
    if event.fpl_id is not None:
        return ("id", event.fpl_id)
    return ("mention", normalise(event.mention))


def _match_pairs(
    expected: Sequence[ExpectedEvent], predicted: Sequence[ExpectedEvent]
) -> list[tuple[ExpectedEvent, ExpectedEvent]]:
    """Pairs of matching events (same player key and event type), one predicted per expected."""
    remaining = list(predicted)
    pairs: list[tuple[ExpectedEvent, ExpectedEvent]] = []
    # First pass takes identical certainties, so the certainty table is not skewed by order.
    unmatched: list[ExpectedEvent] = []
    for exp in expected:
        found = next(
            (
                p
                for p in remaining
                if _key(p) == _key(exp)
                and p.event_type == exp.event_type
                and p.certainty == exp.certainty
            ),
            None,
        )
        if found is None:
            unmatched.append(exp)
        else:
            remaining.remove(found)
            pairs.append((exp, found))
    for exp in unmatched:
        found = next(
            (p for p in remaining if _key(p) == _key(exp) and p.event_type == exp.event_type),
            None,
        )
        if found is not None:
            remaining.remove(found)
            pairs.append((exp, found))
    return pairs


def _mentions_pair(a: str, b: str) -> bool:
    tokens_a, tokens_b = set(normalise(a).split()), set(normalise(b).split())
    if not tokens_a or not tokens_b:
        return False
    return tokens_a <= tokens_b or tokens_b <= tokens_a


def _link_pairs(
    expected: Sequence[ExpectedEvent], predicted: Sequence[ExpectedEvent]
) -> list[tuple[ExpectedEvent, ExpectedEvent]]:
    linked = [e for e in expected if e.fpl_id is not None]
    remaining = list(predicted)
    pairs: list[tuple[ExpectedEvent, ExpectedEvent]] = []
    unpaired: list[ExpectedEvent] = []
    for exp in linked:
        found = next((p for p in remaining if p.fpl_id == exp.fpl_id), None)
        if found is None:
            unpaired.append(exp)
        else:
            remaining.remove(found)
            pairs.append((exp, found))
    for exp in unpaired:
        found = next((p for p in remaining if _mentions_pair(p.mention, exp.mention)), None)
        if found is not None:
            remaining.remove(found)
            pairs.append((exp, found))
    return pairs


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _nearest_rank(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _counts(values: Iterable[str | None]) -> dict[str, int]:
    return dict(Counter(value for value in values if value is not None))


def compute_metrics(
    results: Sequence[CaseResult], posts_per_month: float, usd_pln_rate: float
) -> Metrics:
    true_positives = 0
    expected_total = 0
    predicted_total = 0
    linking_paired = 0
    linking_correct = 0
    empty_cases = 0
    false_alarms = 0
    certainty_matched = 0
    certainty_correct = 0
    confusion = {e: {p: 0 for p in CERTAINTY_LEVELS} for e in CERTAINTY_LEVELS}

    for result in results:
        expected_total += len(result.expected)
        predicted_total += len(result.predicted)

        pairs = _match_pairs(result.expected, result.predicted)
        true_positives += len(pairs)
        for exp, pred in pairs:
            confusion[exp.certainty][pred.certainty] += 1
            certainty_matched += 1
            certainty_correct += exp.certainty == pred.certainty

        for exp, pred in _link_pairs(result.expected, result.predicted):
            linking_paired += 1
            linking_correct += exp.fpl_id == pred.fpl_id

        if not result.expected and result.error_class is None:
            empty_cases += 1
            false_alarms += bool(result.predicted)

    precision = _ratio(true_positives, predicted_total)
    recall = _ratio(true_positives, expected_total)
    f1 = _ratio(2 * true_positives, predicted_total + expected_total)
    linking_accuracy = _ratio(linking_correct, linking_paired)
    false_alarm_rate = _ratio(false_alarms, empty_cases)

    latencies = [r.latency_seconds for r in results if r.latency_seconds is not None]
    mean_cost = _mean([r.cost_usd for r in results if r.cost_usd is not None])
    projected = mean_cost * posts_per_month * usd_pln_rate if mean_cost is not None else None
    errored_cases = sum(1 for r in results if r.error_class is not None)
    flags = threshold_flags(f1, linking_accuracy, false_alarm_rate, projected, errored_cases)
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    reported_costs = [r.reported_cost_usd for r in results if r.reported_cost_usd is not None]

    return Metrics(
        cases=len(results),
        errored_cases=errored_cases,
        precision=precision,
        recall=recall,
        f1=f1,
        linking_accuracy=linking_accuracy,
        linking_paired=linking_paired,
        false_alarm_rate=false_alarm_rate,
        certainty_accuracy=_ratio(certainty_correct, certainty_matched),
        certainty_confusion=confusion,
        latency_p50_seconds=_nearest_rank(latencies, 50),
        latency_p95_seconds=_nearest_rank(latencies, 95),
        mean_input_tokens=_mean([r.input_tokens for r in results if r.input_tokens is not None]),
        mean_output_tokens=_mean([r.output_tokens for r in results if r.output_tokens is not None]),
        mean_cost_usd=mean_cost,
        projected_monthly_cost_pln=projected,
        posts_per_month=posts_per_month,
        usd_pln_rate=usd_pln_rate,
        passes=all(flags.values()),
        mean_reasoning_tokens=_mean(
            [r.reasoning_tokens for r in results if r.reasoning_tokens is not None]
        ),
        mean_reported_cost_usd=_mean(reported_costs),
        total_cost_usd=sum(costs),
        total_reported_cost_usd=sum(reported_costs),
        hosts=_counts(r.host for r in results),
        answered_models=_counts(r.answered_model for r in results),
        thresholds_passed=flags,
    )
