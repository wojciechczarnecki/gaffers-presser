from datetime import UTC, datetime

import pytest

from app.extraction.evaluation.cases import EvalCase, ExpectedEvent
from app.extraction.evaluation.compare import compare_sets
from app.extraction.evaluation.metrics import CaseResult, compute_metrics


def event(mention="Salah", fpl_id=1, event_type="out", certainty="confirmed"):
    return ExpectedEvent(mention=mention, fpl_id=fpl_id, event_type=event_type, certainty=certainty)


def case(case_id, events=(), split="dev"):
    return EvalCase(
        id=case_id,
        author_handle="h",
        text="t",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        is_repost=False,
        is_reply=False,
        split=split,
        synthetic=False,
        reviewed=True,
        tags=[],
        expected_events=list(events),
    )


def by_split(comparisons):
    return {c.split: c for c in comparisons}


def test_identical_sets_have_zero_changes_and_perfect_f1():
    cases = [case("a", [event()]), case("b")]
    result = by_split(compare_sets(cases, cases))["dev"]
    assert (result.cases, result.cases_changed) == (2, 0)
    assert (result.added, result.removed, result.relabelled) == (0, 0, 0)
    assert result.f1 == 1.0
    assert result.only_in_reviewed == [] and result.only_in_baseline == []


def test_one_event_added_and_one_removed():
    reviewed = [case("a", [event("Salah", 1), event("Haaland", 2)]), case("b")]
    baseline = [case("a", [event("Salah", 1)]), case("b", [event("Saka", 3)])]
    result = by_split(compare_sets(reviewed, baseline))["dev"]
    assert (result.cases, result.cases_changed) == (2, 2)
    assert (result.added, result.removed, result.relabelled) == (1, 1, 0)


def test_relabelled_on_certainty_and_on_fpl_id():
    reviewed = [
        case("a", [event("Salah", 1, certainty="likely")]),
        case("b", [event("Palmer", 5)]),
    ]
    baseline = [
        case("a", [event("Salah", 1, certainty="confirmed")]),
        case("b", [event("Palmer", None)]),
    ]
    result = by_split(compare_sets(reviewed, baseline))["dev"]
    assert (result.added, result.removed, result.relabelled) == (0, 0, 2)
    assert result.relabelled_by_field == {"event_type": 0, "certainty": 1, "fpl_id": 1}


def test_relabelled_on_event_type_paired_by_fpl_id_despite_other_mention():
    reviewed = [case("a", [event("Mo Salah", 1, event_type="doubt")])]
    baseline = [case("a", [event("Salah", 1, event_type="out")])]
    result = by_split(compare_sets(reviewed, baseline))["dev"]
    assert result.relabelled == 1
    assert result.relabelled_by_field["event_type"] == 1


def test_case_missing_from_one_side_is_listed():
    reviewed = [case("a"), case("new")]
    baseline = [case("a"), case("gone")]
    result = by_split(compare_sets(reviewed, baseline))["dev"]
    assert result.only_in_reviewed == ["new"]
    assert result.only_in_baseline == ["gone"]
    assert result.cases == 2


def test_grouped_per_split():
    reviewed = [case("a", [event()], split="dev"), case("b", [event()], split="test")]
    baseline = [case("a", [event()], split="dev"), case("b", [], split="test")]
    result = by_split(compare_sets(reviewed, baseline))
    assert set(result) == {"dev", "test"}
    assert result["dev"].cases_changed == 0
    assert (result["test"].cases_changed, result["test"].added) == (1, 1)


def test_f1_equals_compute_metrics_on_the_same_pairs():
    reviewed = [
        case("a", [event("Salah", 1), event("Haaland", 2)]),
        case("b"),
        case("c", [event("Saka", 3, certainty="likely")]),
    ]
    baseline = [
        case("a", [event("Salah", 1)]),
        case("b", [event("Kane", 4)]),
        case("c", [event("Saka", 3, certainty="rumour")]),
    ]
    result = by_split(compare_sets(reviewed, baseline))["dev"]
    expected = compute_metrics(
        [
            CaseResult(expected=r.expected_events, predicted=b.expected_events)
            for r, b in zip(reviewed, baseline, strict=True)
        ],
        posts_per_month=1,
        usd_pln_rate=1,
    )
    assert result.precision == pytest.approx(expected.precision)
    assert result.recall == pytest.approx(expected.recall)
    assert result.f1 == pytest.approx(expected.f1)
    assert result.f1 == pytest.approx(2 / 3)
