from collections.abc import Sequence
from dataclasses import dataclass

from app.retrieval.evaluation.dataset import FreeQuery, Judgement, PlayerQuery

OWNER = "owner"

AnyQuery = FreeQuery | PlayerQuery


def reviewed_entry(judgements: Sequence[Judgement], x_id: int) -> Judgement | None:
    found = [j for j in judgements if j.x_id == x_id and j.reviewed]
    return found[-1] if found else None


def judge_entry(judgements: Sequence[Judgement], x_id: int, judge: str) -> Judgement | None:
    found = [j for j in judgements if j.x_id == x_id and not j.reviewed and j.labelled_by == judge]
    return found[-1] if found else None


def label_prompt_versions(
    queries: Sequence[AnyQuery], reference_judge: str
) -> dict[str, list[int]]:
    # The relevance prompt versions behind the judge labels a run measures against; labels
    # the owner reviewed carry no prompt.
    versions: dict[str, set[int]] = {"player": set(), "free": set()}
    for query in queries:
        name = "player" if isinstance(query, PlayerQuery) else "free"
        for x_id in dict.fromkeys(j.x_id for j in query.judgements):
            if reviewed_entry(query.judgements, x_id) is not None:
                continue
            entry = judge_entry(query.judgements, x_id, reference_judge)
            if entry is not None and entry.prompt_version is not None:
                versions[name].add(entry.prompt_version)
    return {name: sorted(found) for name, found in versions.items()}


def reference_labels(query: AnyQuery, reference_judge: str) -> dict[int, bool]:
    labels: dict[int, bool] = {}
    for x_id in dict.fromkeys(j.x_id for j in query.judgements):
        entry = reviewed_entry(query.judgements, x_id) or judge_entry(
            query.judgements, x_id, reference_judge
        )
        if entry is not None:
            labels[x_id] = entry.relevant
    return labels


@dataclass(frozen=True)
class LabelCounts:
    owner_reviewed: int
    judge_only: int
    judge_owner_agreement: float | None
    reviewed_pairs_compared: int


def label_counts(queries: Sequence[AnyQuery], reference_judge: str) -> LabelCounts:
    reviewed = judge_only = compared = agreed = 0
    for query in queries:
        for x_id in dict.fromkeys(j.x_id for j in query.judgements):
            owner = reviewed_entry(query.judgements, x_id)
            judge = judge_entry(query.judgements, x_id, reference_judge)
            if owner is not None:
                reviewed += 1
                if judge is not None:
                    compared += 1
                    agreed += owner.relevant == judge.relevant
            elif judge is not None:
                judge_only += 1
    return LabelCounts(
        owner_reviewed=reviewed,
        judge_only=judge_only,
        judge_owner_agreement=agreed / compared if compared else None,
        reviewed_pairs_compared=compared,
    )
