import pytest

from app.extraction.evaluation.cases import ExpectedEvent
from app.extraction.evaluation.metrics import (
    MAX_MONTHLY_COST_PLN,
    CaseResult,
    compute_metrics,
    passes,
    threshold_flags,
)


def ev(mention="Haaland", fpl_id=5, event_type="out", certainty="confirmed") -> ExpectedEvent:
    return ExpectedEvent(mention=mention, fpl_id=fpl_id, event_type=event_type, certainty=certainty)


def run(results, posts_per_month=1000, rate=4.0):
    return compute_metrics(results, posts_per_month=posts_per_month, usd_pln_rate=rate)


def test_perfect_run():
    metrics = run(
        [
            CaseResult(expected=[ev()], predicted=[ev()]),
            CaseResult(expected=[], predicted=[]),
        ]
    )
    assert (metrics.precision, metrics.recall, metrics.f1) == (1.0, 1.0, 1.0)
    assert metrics.linking_accuracy == 1.0
    assert metrics.false_alarm_rate == 0.0
    assert metrics.certainty_accuracy == 1.0
    assert metrics.cases == 2


def test_missed_event_lowers_recall():
    metrics = run([CaseResult(expected=[ev(), ev("Saka", 7)], predicted=[ev()])])
    assert metrics.precision == 1.0
    assert metrics.recall == 0.5
    assert metrics.f1 == pytest.approx(2 / 3)


def test_extra_event_lowers_precision():
    metrics = run([CaseResult(expected=[ev()], predicted=[ev(), ev("Saka", 7)])])
    assert metrics.precision == 0.5
    assert metrics.recall == 1.0
    assert metrics.f1 == pytest.approx(2 / 3)


def test_wrong_event_type_is_no_match():
    metrics = run([CaseResult(expected=[ev()], predicted=[ev(event_type="doubt")])])
    assert (metrics.precision, metrics.recall, metrics.f1) == (0.0, 0.0, 0.0)


def test_unlinked_events_match_on_normalised_mention():
    expected = ev("Ødegaard", None)
    predicted = ev("odegaard", None)
    metrics = run([CaseResult(expected=[expected], predicted=[predicted])])
    assert metrics.f1 == 1.0


def test_linked_expected_does_not_match_an_unlinked_prediction():
    metrics = run([CaseResult(expected=[ev("Haaland", 5)], predicted=[ev("Haaland", None)])])
    assert metrics.f1 == 0.0


def test_duplicate_events_are_counted_as_a_multiset():
    metrics = run([CaseResult(expected=[ev()], predicted=[ev(), ev()])])
    assert metrics.precision == 0.5
    assert metrics.recall == 1.0


def test_linking_accuracy_counts_wrong_fpl_id_and_ignores_unpaired():
    wrong = CaseResult(
        expected=[ev("Haaland", 5), ev("Saka", 7)], predicted=[ev("Haaland", 6)]
    )  # Haaland paired but wrong; Saka unpaired
    right = CaseResult(expected=[ev("Salah", 9)], predicted=[ev("Salah", 9)])
    metrics = run([wrong, right])
    assert metrics.linking_paired == 2
    assert metrics.linking_accuracy == 0.5


def test_linking_pairs_on_token_containment():
    metrics = run([CaseResult(expected=[ev("Bruno Fernandes", 3)], predicted=[ev("Bruno", 4)])])
    assert metrics.linking_paired == 1
    assert metrics.linking_accuracy == 0.0


def test_linking_pairs_on_equal_fpl_id_even_with_a_different_mention():
    metrics = run([CaseResult(expected=[ev("KDB", 8)], predicted=[ev("De Bruyne", 8)])])
    assert metrics.linking_paired == 1
    assert metrics.linking_accuracy == 1.0


def test_false_alarms_only_over_cases_without_expected_events():
    results = [
        CaseResult(expected=[], predicted=[]),
        CaseResult(expected=[], predicted=[]),
        CaseResult(expected=[], predicted=[]),
        CaseResult(expected=[], predicted=[ev()]),
        CaseResult(expected=[ev()], predicted=[ev()]),
    ]
    assert run(results).false_alarm_rate == 0.25


def test_no_empty_cases_gives_zero_false_alarm_rate():
    assert run([CaseResult(expected=[ev()], predicted=[ev()])]).false_alarm_rate == 0.0


def test_certainty_accuracy_and_confusion_over_matched_events():
    results = [
        CaseResult(expected=[ev(certainty="confirmed")], predicted=[ev(certainty="confirmed")]),
        CaseResult(expected=[ev(certainty="likely")], predicted=[ev(certainty="rumour")]),
        CaseResult(expected=[ev(certainty="rumour")], predicted=[ev(certainty="rumour")]),
        CaseResult(expected=[ev("Saka", 7)], predicted=[]),  # unmatched: not in the table
    ]
    metrics = run(results)
    assert metrics.certainty_accuracy == pytest.approx(2 / 3)
    assert metrics.certainty_confusion["confirmed"] == {"confirmed": 1, "likely": 0, "rumour": 0}
    assert metrics.certainty_confusion["likely"] == {"confirmed": 0, "likely": 0, "rumour": 1}
    assert metrics.certainty_confusion["rumour"] == {"confirmed": 0, "likely": 0, "rumour": 1}


def test_latency_percentiles_nearest_rank():
    results = [
        CaseResult(expected=[], predicted=[], latency_seconds=float(i)) for i in range(1, 21)
    ]
    metrics = run(results)
    assert metrics.latency_p50_seconds == 10.0
    assert metrics.latency_p95_seconds == 19.0


def test_tokens_cost_and_projection():
    results = [
        CaseResult(expected=[], predicted=[], input_tokens=100, output_tokens=10, cost_usd=0.001),
        CaseResult(expected=[], predicted=[], input_tokens=300, output_tokens=30, cost_usd=0.003),
    ]
    metrics = run(results, posts_per_month=1000, rate=4.0)
    assert metrics.mean_input_tokens == 200
    assert metrics.mean_output_tokens == 20
    assert metrics.mean_cost_usd == pytest.approx(0.002)
    assert metrics.projected_monthly_cost_pln == pytest.approx(8.0)


def test_unknown_cost_gives_no_projection_and_no_pass():
    metrics = run([CaseResult(expected=[ev()], predicted=[ev()])])
    assert metrics.mean_cost_usd is None
    assert metrics.projected_monthly_cost_pln is None
    assert metrics.passes is False


def test_errored_case_counts_as_missed_events():
    results = [
        CaseResult(expected=[ev()], predicted=[], error_class="RateLimitError"),
        CaseResult(expected=[ev()], predicted=[ev()]),
    ]
    metrics = run(results)
    assert metrics.errored_cases == 1
    assert metrics.recall == 0.5


def test_errored_case_without_expected_events_is_not_a_correct_negative():
    results = [
        CaseResult(expected=[], predicted=[], error_class="RateLimitError"),
        CaseResult(expected=[], predicted=[ev()]),
    ]
    metrics = run(results)
    assert metrics.errored_cases == 1
    assert metrics.false_alarm_rate == 1.0


def test_errored_case_fails_the_thresholds():
    ok = CaseResult(expected=[ev()], predicted=[ev()], cost_usd=0.0001)
    assert run([ok]).passes is True
    errored = CaseResult(expected=[], predicted=[], error_class="RateLimitError")
    assert run([ok, errored]).passes is False
    assert passes(0.85, 0.95, 0.05, 5.0, errored_cases=1) is False


def test_passes_at_the_edges():
    assert passes(0.85, 0.95, 0.05, 5.0) is True
    assert passes(0.8499, 0.95, 0.05, 5.0) is False
    assert passes(0.85, 0.9499, 0.05, 5.0) is False
    assert passes(0.85, 0.95, 0.0501, 5.0) is False
    assert passes(0.85, 0.95, 0.05, 5.01) is False
    assert passes(0.85, 0.95, 0.05, None) is False


def test_exact_edge_ratios_are_not_lost_to_float_error():
    # F1: 17 of 20 events match -> 34 / 40 = 0.85 exactly.
    cases = [
        CaseResult(expected=[ev(f"P{i}", i)], predicted=[ev(f"P{i}", i)]) for i in range(17)
    ] + [
        CaseResult(expected=[ev(f"P{i}", i)], predicted=[ev(f"P{i}", i, event_type="doubt")])
        for i in range(17, 20)
    ]
    assert run(cases).f1 == 0.85
    # Linking: 19 of 20 paired events carry the right id -> 0.95 exactly.
    linked = [
        CaseResult(expected=[ev(f"P{i}", i)], predicted=[ev(f"P{i}", i)]) for i in range(19)
    ] + [CaseResult(expected=[ev("P19", 19)], predicted=[ev("P19", 99)])]
    assert run(linked).linking_accuracy == 0.95
    # False alarms: 1 of 20 empty cases -> 0.05 exactly.
    empty = [CaseResult(expected=[], predicted=[ev()])] + [
        CaseResult(expected=[], predicted=[]) for _ in range(19)
    ]
    assert run(empty).false_alarm_rate == 0.05


def test_thresholds_passed_per_threshold():
    ok = dict(f1=0.85, linking_accuracy=0.95, false_alarm_rate=0.05, monthly_cost_pln=5.0)
    assert threshold_flags(**ok) == {
        "f1": True,
        "linking_accuracy": True,
        "false_alarm_rate": True,
        "monthly_cost": True,
        "no_errored_cases": True,
    }
    assert threshold_flags(**{**ok, "f1": 0.849})["f1"] is False
    assert threshold_flags(**{**ok, "linking_accuracy": 0.949})["linking_accuracy"] is False
    assert threshold_flags(**{**ok, "false_alarm_rate": 0.051})["false_alarm_rate"] is False
    assert threshold_flags(**{**ok, "monthly_cost_pln": 5.01})["monthly_cost"] is False
    assert threshold_flags(**{**ok, "monthly_cost_pln": None})["monthly_cost"] is False
    assert threshold_flags(**ok, errored_cases=1)["no_errored_cases"] is False
    flags = threshold_flags(**{**ok, "f1": 0.5})
    assert [name for name, value in flags.items() if not value] == ["f1"]
    assert passes(**ok) is True
    assert passes(**{**ok, "f1": 0.5}) is False


def test_metrics_carry_thresholds_and_passes_is_their_conjunction():
    metrics = run(
        [CaseResult(expected=[ev()], predicted=[ev()], input_tokens=1, output_tokens=1, cost_usd=0)]
    )
    assert metrics.thresholds_passed["f1"] is True
    assert metrics.passes is all(metrics.thresholds_passed.values())
    over_budget = run([CaseResult(expected=[], predicted=[], cost_usd=1.0)], posts_per_month=1000)
    assert over_budget.projected_monthly_cost_pln > MAX_MONTHLY_COST_PLN
    assert over_budget.thresholds_passed["monthly_cost"] is False
    assert over_budget.passes is False


def test_mean_reasoning_tokens_and_reported_cost():
    results = [
        CaseResult(expected=[], predicted=[], reasoning_tokens=10, reported_cost_usd=0.001),
        CaseResult(expected=[], predicted=[], reasoning_tokens=30, reported_cost_usd=0.003),
        CaseResult(expected=[], predicted=[], error_class="RuntimeError"),
    ]
    metrics = run(results)
    assert metrics.mean_reasoning_tokens == 20
    assert metrics.mean_reported_cost_usd == pytest.approx(0.002)
    assert metrics.total_reported_cost_usd == pytest.approx(0.004)


def test_no_reasoning_tokens_gives_none():
    metrics = run([CaseResult(expected=[], predicted=[])])
    assert metrics.mean_reasoning_tokens is None
    assert metrics.mean_reported_cost_usd is None


def test_hosts_and_answered_models_counted():
    results = [
        CaseResult(expected=[], predicted=[], host="DeepInfra", answered_model="a/m"),
        CaseResult(expected=[], predicted=[], host="DeepInfra", answered_model="a/m"),
        CaseResult(expected=[], predicted=[], host="Together", answered_model="b/fallback"),
        CaseResult(expected=[], predicted=[]),
    ]
    metrics = run(results)
    assert metrics.hosts == {"DeepInfra": 2, "Together": 1}
    assert metrics.answered_models == {"a/m": 2, "b/fallback": 1}


def test_totals_sum_case_costs():
    results = [
        CaseResult(expected=[], predicted=[], cost_usd=0.25),
        CaseResult(expected=[], predicted=[], cost_usd=0.5),
        CaseResult(expected=[], predicted=[]),
    ]
    assert run(results).total_cost_usd == pytest.approx(0.75)
