import pytest

from app.retrieval.evaluation.dataset import FreeQuery, Judgement
from app.retrieval.evaluation.labels import label_counts, reference_labels


def j(x_id, relevant, by, reviewed=False):
    return Judgement(x_id=x_id, relevant=relevant, reviewed=reviewed, labelled_by=by)


def query(*judgements):
    return FreeQuery(
        id="q",
        text="t",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="dev",
        judgements=list(judgements),
    )


def test_an_owner_entry_beats_the_judges():
    q = query(j(1, True, "a"), j(1, False, "b"), j(1, False, "owner", reviewed=True))
    assert reference_labels(q, "a") == {1: False}


def test_the_reference_judge_is_used_without_an_owner_entry():
    q = query(j(1, True, "a"), j(1, False, "b"), j(2, False, "a"))
    assert reference_labels(q, "a") == {1: True, 2: False}
    assert reference_labels(q, "b") == {1: False}


def test_another_judge_alone_leaves_the_pair_unjudged():
    assert reference_labels(query(j(1, True, "b")), "a") == {}


def test_a_reviewed_v1_entry_counts_as_the_owners():
    assert reference_labels(query(j(1, True, "chat/model", reviewed=True)), "a") == {1: True}


def test_counts_and_agreement():
    q1 = query(
        j(1, True, "a"),
        j(1, True, "owner", reviewed=True),  # agrees
        j(2, True, "a"),
        j(2, False, "owner", reviewed=True),  # disagrees
        j(3, True, "a"),  # judge only
        j(4, False, "owner", reviewed=True),  # owner only: no judge label to compare
    )
    q2 = query(j(5, False, "a"), j(5, False, "b"))
    counts = label_counts([q1, q2], "a")
    assert counts.owner_reviewed == 3
    assert counts.judge_only == 2
    assert counts.reviewed_pairs_compared == 2
    assert counts.judge_owner_agreement == pytest.approx(0.5)


def test_agreement_is_none_without_reviewed_pairs():
    assert label_counts([query(j(1, True, "a"))], "a").judge_owner_agreement is None
