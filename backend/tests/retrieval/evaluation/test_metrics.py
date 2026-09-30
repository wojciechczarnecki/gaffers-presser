import pytest

from app.retrieval.evaluation.metrics import (
    QueryOutcome,
    aggregate,
    recall_at_k,
    reciprocal_rank,
)


def test_no_relevant_in_top_k():
    ranked = [11, 12, 13, 14, 15, 1]
    assert recall_at_k(ranked, {1}, 5) == 0.0
    assert recall_at_k(ranked, {1}, 10) == 1.0
    assert reciprocal_rank([11, 12, 13], {1}) == 0.0


def test_first_relevant_at_rank_3():
    ranked = [11, 12, 1, 13]
    assert reciprocal_rank(ranked, {1}) == pytest.approx(1 / 3)
    assert recall_at_k(ranked, {1}, 5) == 1.0


def test_several_relevant():
    ranked = [1, 20, 2, 21, 22, 23, 24, 25, 26, 3]
    relevant = {1, 2, 3}
    assert recall_at_k(ranked, relevant, 5) == pytest.approx(2 / 3)
    assert recall_at_k(ranked, relevant, 10) == 1.0
    assert reciprocal_rank(ranked, relevant) == 1.0


def _outcome(id_, language, relevant, **ranked):
    return QueryOutcome(
        id=id_,
        language=language,
        origin="post",
        relevant=frozenset(relevant),
        ranked={"fulltext": [], "vector": [], "hybrid": []} | ranked,
    )


def test_aggregate_slices_and_excludes_empty():
    outcomes = [
        _outcome("en-1", "en", {1}, fulltext=[1], vector=[9, 1], hybrid=[1]),
        _outcome("en-2", "en", {2, 3}, fulltext=[8, 2], vector=[2, 3], hybrid=[2]),
        _outcome("pl-1", "pl", {4}, fulltext=[], vector=[4], hybrid=[4]),
        _outcome("en-3", "en", set(), fulltext=[1]),
    ]

    result = aggregate(outcomes)

    assert result.queries_without_relevant == 1
    fulltext = result.modes["fulltext"]
    assert fulltext["all"].n == 3
    assert fulltext["en"].n == 2
    assert fulltext["pl"].n == 1
    assert fulltext["en"].mrr == pytest.approx((1 + 0.5) / 2)
    assert fulltext["en"].recall_at_5 == pytest.approx((1 + 0.5) / 2)
    assert fulltext["pl"].mrr == 0.0
    assert fulltext["all"].mrr == pytest.approx((1 + 0.5 + 0) / 3)
    vector = result.modes["vector"]
    assert vector["all"].mrr == pytest.approx((0.5 + 1 + 1) / 3)
    assert vector["pl"].recall_at_10 == 1.0
    assert result.modes["hybrid"]["en"].recall_at_5 == pytest.approx((1 + 0.5) / 2)


def test_aggregate_with_nothing_evaluable():
    result = aggregate([_outcome("en-1", "en", set())])
    assert result.queries_without_relevant == 1
    assert result.modes["fulltext"]["all"].n == 0
    assert result.modes["fulltext"]["all"].mrr == 0.0
