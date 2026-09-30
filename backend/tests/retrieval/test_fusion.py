import pytest

from app.retrieval.search import fuse


def test_fuse_matches_hand_computed_rrf():
    fused = fuse({"fulltext": [1, 2], "vector": [3, 2, 1]}, k=60)
    assert [x_id for x_id, _, _ in fused] == [1, 2, 3]
    scores = {x_id: score for x_id, score, _ in fused}
    assert scores[1] == pytest.approx(1 / 61 + 1 / 63)
    assert scores[2] == pytest.approx(1 / 62 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 61)
    ranks = {x_id: legs for x_id, _, legs in fused}
    assert ranks[1] == {"fulltext": 1, "vector": 3}
    assert ranks[2] == {"fulltext": 2, "vector": 2}
    assert ranks[3] == {"vector": 1}


def test_single_leg_post_still_appears():
    fused = fuse({"fulltext": [7], "vector": []}, k=60)
    assert fused == [(7, pytest.approx(1 / 61), {"fulltext": 1})]


def test_one_ranking_keeps_its_order():
    fused = fuse({"vector": [9, 4, 6]}, k=60)
    assert [x_id for x_id, _, _ in fused] == [9, 4, 6]


def test_ties_broken_by_best_rank_then_newer_id():
    # k=0: 20 and 30 score 1/1 and 10 scores 1/2 + 1/2, so all three tie exactly at 1.0.
    # 10 loses on its worse best rank; 20 and 30 share best rank 1, so the newer id wins.
    tied = fuse({"fulltext": [20, 10], "vector": [30, 10]}, k=0)
    assert {score for _, score, _ in tied} == {1.0}
    assert [x_id for x_id, _, _ in tied] == [30, 20, 10]


def test_k_changes_scores():
    low = dict((x_id, score) for x_id, score, _ in fuse({"fulltext": [1]}, k=1))
    high = dict((x_id, score) for x_id, score, _ in fuse({"fulltext": [1]}, k=100))
    assert low[1] == pytest.approx(1 / 2)
    assert high[1] == pytest.approx(1 / 101)
