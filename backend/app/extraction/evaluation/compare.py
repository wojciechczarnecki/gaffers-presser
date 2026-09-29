from collections.abc import Sequence
from dataclasses import dataclass

from app.extraction.evaluation.cases import EvalCase, ExpectedEvent
from app.extraction.evaluation.metrics import CaseResult, compute_metrics
from app.extraction.linking import normalise

FIELDS = ("event_type", "certainty", "fpl_id")


@dataclass(frozen=True)
class EventChange:
    case_id: str
    mention: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class SplitComparison:
    split: str
    cases: int
    cases_changed: int
    added: int
    removed: int
    relabelled: int
    relabelled_by_field: dict[str, int]
    only_in_reviewed: list[str]
    only_in_baseline: list[str]
    precision: float
    recall: float
    f1: float


def _identity(event: ExpectedEvent) -> tuple[str, int | None, str, str]:
    return (event.mention, event.fpl_id, event.event_type, event.certainty)


def _same_player(a: ExpectedEvent, b: ExpectedEvent) -> bool:
    if a.fpl_id is not None and a.fpl_id == b.fpl_id:
        return True
    return normalise(a.mention) == normalise(b.mention)


def compare_events(
    case_id: str, reviewed: Sequence[ExpectedEvent], baseline: Sequence[ExpectedEvent]
) -> tuple[int, int, list[EventChange]]:
    reviewed_left = list(reviewed)
    baseline_left = list(baseline)
    for event in list(reviewed_left):
        match = next((b for b in baseline_left if _identity(b) == _identity(event)), None)
        if match is not None:
            reviewed_left.remove(event)
            baseline_left.remove(match)

    relabelled: list[EventChange] = []
    for event in list(reviewed_left):
        match = next((b for b in baseline_left if _same_player(event, b)), None)
        if match is None:
            continue
        reviewed_left.remove(event)
        baseline_left.remove(match)
        fields = tuple(f for f in FIELDS if getattr(event, f) != getattr(match, f))
        relabelled.append(EventChange(case_id, event.mention, fields))
    return len(reviewed_left), len(baseline_left), relabelled


def compare_sets(
    reviewed: Sequence[EvalCase], baseline: Sequence[EvalCase]
) -> list[SplitComparison]:
    baseline_by_id = {case.id: case for case in baseline}
    reviewed_ids = {case.id for case in reviewed}

    splits: dict[str, dict] = {}

    def bucket(split: str) -> dict:
        return splits.setdefault(
            split,
            {
                "cases": 0,
                "changed": 0,
                "added": 0,
                "removed": 0,
                "relabelled": 0,
                "by_field": {f: 0 for f in FIELDS},
                "only_reviewed": [],
                "only_baseline": [],
                "results": [],
            },
        )

    for case in reviewed:
        b = bucket(case.split)
        b["cases"] += 1
        other = baseline_by_id.get(case.id)
        if other is None:
            b["only_reviewed"].append(case.id)
            continue
        added, removed, relabelled = compare_events(
            case.id, case.expected_events, other.expected_events
        )
        b["added"] += added
        b["removed"] += removed
        b["relabelled"] += len(relabelled)
        for change in relabelled:
            for field_name in change.fields:
                b["by_field"][field_name] += 1
        b["changed"] += bool(added or removed or relabelled)
        b["results"].append(
            CaseResult(expected=case.expected_events, predicted=other.expected_events)
        )
    for case in baseline:
        if case.id not in reviewed_ids:
            bucket(case.split)["only_baseline"].append(case.id)

    comparisons = []
    for split in sorted(splits):
        b = splits[split]
        metrics = compute_metrics(b["results"], posts_per_month=0, usd_pln_rate=0)
        comparisons.append(
            SplitComparison(
                split=split,
                cases=b["cases"],
                cases_changed=b["changed"],
                added=b["added"],
                removed=b["removed"],
                relabelled=b["relabelled"],
                relabelled_by_field=b["by_field"],
                only_in_reviewed=b["only_reviewed"],
                only_in_baseline=b["only_baseline"],
                precision=metrics.precision,
                recall=metrics.recall,
                f1=metrics.f1,
            )
        )
    return comparisons
