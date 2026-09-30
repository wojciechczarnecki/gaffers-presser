import pytest

from app.corroboration.evaluation.metrics import Outcome, compute_metrics


def _o(n, expected, predicted, error=None):
    return Outcome(f"c{n}", expected, predicted, error_class=error)


OUTCOMES = [
    _o(1, "supports", "supports"),
    _o(2, "supports", "related"),
    _o(3, "related", "related"),
    _o(4, "unrelated", "supports"),
    _o(5, "contradicts", "contradicts"),
    _o(6, "unrelated", "unrelated"),
    _o(7, "related", None, error="RuntimeError"),
    _o(8, "supports", "supports"),
]


def test_accuracy_counts_an_errored_case_as_wrong():
    metrics = compute_metrics(OUTCOMES)
    assert metrics["cases"] == 8
    assert metrics["errored"] == 1
    assert metrics["accuracy"] == pytest.approx(5 / 8)


def test_per_label_precision_and_recall_by_hand():
    per_label = compute_metrics(OUTCOMES)["per_label"]
    # supports: predicted on 1, 4, 8 (two right); expected on 1, 2, 8
    assert per_label["supports"]["precision"] == pytest.approx(2 / 3)
    assert per_label["supports"]["recall"] == pytest.approx(2 / 3)
    # related: predicted on 2, 3 (one right); expected on 3, 7 (7 errored)
    assert per_label["related"]["precision"] == pytest.approx(1 / 2)
    assert per_label["related"]["recall"] == pytest.approx(1 / 2)
    assert per_label["contradicts"]["precision"] == 1.0
    assert per_label["contradicts"]["recall"] == 1.0
    # unrelated: predicted on 6; expected on 4, 6
    assert per_label["unrelated"]["precision"] == 1.0
    assert per_label["unrelated"]["recall"] == pytest.approx(1 / 2)
    assert per_label["supports"]["expected"] == 3
    assert per_label["supports"]["predicted"] == 3


def test_the_false_support_rate_numerator_and_denominator():
    false_support = compute_metrics(OUTCOMES)["false_support"]
    # expected unrelated/related/contradicts: cases 3, 4, 5, 6, 7; only case 4 says supports
    assert false_support == {"numerator": 1, "denominator": 5, "rate": pytest.approx(0.2)}


def test_zero_division_gives_none():
    metrics = compute_metrics([_o(1, "contradicts", "supports"), _o(2, "supports", "supports")])
    assert metrics["per_label"]["contradicts"]["precision"] is None
    assert metrics["per_label"]["contradicts"]["recall"] == 0.0
    assert metrics["per_label"]["related"]["precision"] is None
    assert metrics["per_label"]["related"]["recall"] is None
    only_supports = compute_metrics([_o(1, "supports", "supports")])
    assert only_supports["false_support"] == {"numerator": 0, "denominator": 0, "rate": None}


def test_no_outcomes():
    metrics = compute_metrics([])
    assert metrics["accuracy"] is None
    assert metrics["cases"] == 0
