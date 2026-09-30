from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.corroboration.evaluation.cases import LABELS

FALSE_SUPPORT_EXPECTED = ("unrelated", "related", "contradicts")


@dataclass(frozen=True)
class Outcome:
    id: str
    expected: str
    predicted: str | None
    error_class: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def compute_metrics(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    per_label: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        expected = sum(1 for o in outcomes if o.expected == label)
        predicted = sum(1 for o in outcomes if o.predicted == label)
        true_positive = sum(1 for o in outcomes if o.expected == label and o.predicted == label)
        per_label[label] = {
            "precision": _ratio(true_positive, predicted),
            "recall": _ratio(true_positive, expected),
            "expected": expected,
            "predicted": predicted,
        }
    at_risk = [o for o in outcomes if o.expected in FALSE_SUPPORT_EXPECTED]
    false_supports = sum(1 for o in at_risk if o.predicted == "supports")
    correct = sum(1 for o in outcomes if o.predicted == o.expected)
    return {
        "cases": len(outcomes),
        "errored": sum(1 for o in outcomes if o.predicted is None),
        "accuracy": _ratio(correct, len(outcomes)),
        "per_label": per_label,
        "false_support": {
            "numerator": false_supports,
            "denominator": len(at_risk),
            "rate": _ratio(false_supports, len(at_risk)),
        },
    }
