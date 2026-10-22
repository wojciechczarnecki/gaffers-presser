from datetime import UTC, datetime, timedelta

import pytest

from app.retrieval.evaluation.dataset import ClaimedPost, PlayerInfo, PlayerQuery
from app.retrieval.evaluation.player_metrics import (
    DEFAULT_DEPTHS,
    JUDGE_CUT,
    aggregate_player,
    judge_cut,
    player_counts,
    query_numbers,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def ids(start, count):
    return list(range(start, start + count))


def test_the_constants():
    assert JUDGE_CUT == 10
    assert DEFAULT_DEPTHS == (20, 30, 50)


def test_the_cut_drops_claimed_ids_then_takes_the_first_ten():
    ranked = ids(1, 40)
    assert judge_cut(ranked, {1, 2, 3}, 20) == ids(4, 10)
    assert judge_cut(ranked, set(), 5) == ids(1, 5)
    assert judge_cut(ranked, set(range(1, 18)), 20) == [18, 19, 20]


def test_numbers_at_depths_20_and_50():
    ranked = ids(1, 60)
    labels = {3: True, 5: True, 25: True, 45: True, 4: False, 70: True}
    claimed = {3}
    at_20 = query_numbers(ranked, claimed, labels, 20)
    assert at_20.cut == [1, 2, 4, 5, 6, 7, 8, 9, 10, 11]
    # unclaimed relevant: 5, 25, 45, 70 -> only 5 is in the cut
    assert at_20.recall_at_cut == pytest.approx(1 / 4)
    # raw top 10 holds 3 and 5 of the 5 relevant posts 3, 5, 25, 45, 70
    assert at_20.recall_at_10 == pytest.approx(2 / 5)
    assert not at_20.empty_cut_with_miss
    # cut posts without a label: 1, 2, 6, 7, 8, 9, 10, 11 (4 and 5 are labelled)
    assert at_20.unjudged == 8
    at_50 = query_numbers(ranked, claimed, labels, 50)
    assert at_50.cut == at_20.cut  # the cut stops at ten whatever the depth
    assert at_50.recall_at_cut == at_20.recall_at_cut


def test_more_than_twenty_claimed_posts_empty_the_cut():
    ranked = ids(1, 40)
    claimed = set(range(1, 23))
    labels = {23: True}
    at_20 = query_numbers(ranked, claimed, labels, 20)
    assert at_20.cut == []
    assert at_20.empty_cut_with_miss
    assert at_20.recall_at_cut == 0.0
    at_30 = query_numbers(ranked, claimed, labels, 30)
    assert at_30.cut == [23, 24, 25, 26, 27, 28, 29, 30]
    assert at_30.recall_at_cut == 1.0
    assert not at_30.empty_cut_with_miss


def test_recalls_are_undefined_without_a_reference_set():
    only_claimed = query_numbers(ids(1, 5), {1}, {1: True}, 20)
    assert only_claimed.recall_at_cut is None
    assert only_claimed.recall_at_10 == 1.0
    none_relevant = query_numbers(ids(1, 5), set(), {1: False}, 20)
    assert (none_relevant.recall_at_cut, none_relevant.recall_at_10) == (None, None)
    assert not none_relevant.empty_cut_with_miss


def test_an_empty_cut_without_a_miss_is_not_flagged():
    numbers = query_numbers([1, 2], {1, 2}, {1: True}, 20)
    assert numbers.cut == [] and not numbers.empty_cut_with_miss


def test_aggregate_leaves_undefined_queries_out_of_the_means_and_pools_the_share():
    a = query_numbers(ids(1, 20), set(), {1: True, 2: False}, 20)  # recall 1.0, unjudged 8 of 10
    b = query_numbers(ids(1, 30), set(range(1, 25)), {26: True}, 20)  # empty cut with a miss
    c = query_numbers(
        ids(1, 5), {1}, {1: True}, 20
    )  # recall_at_cut undefined, cut of 4, all unjudged
    d = query_numbers(ids(1, 5), set(), {}, 20)  # nothing relevant
    result = aggregate_player([a, b, c, d])
    assert result.queries == 4
    assert result.recall_at_cut == pytest.approx((1.0 + 0.0) / 2)
    assert result.recall_at_10 == pytest.approx((1.0 + 0.0 + 1.0) / 3)
    assert result.empty_cuts_with_miss == 1
    # unjudged 8 + 0 + 4 + 5 over cuts 10 + 0 + 4 + 5
    assert result.unjudged_share == pytest.approx(17 / 19)


def test_aggregate_of_nothing():
    result = aggregate_player([])
    assert (result.recall_at_cut, result.unjudged_share) == (None, None)


def pq(id_, web_name, claims, judgements=()):
    return PlayerQuery(
        id=id_,
        split="dev",
        player=PlayerInfo(
            season="2026/27",
            fpl_id=1,
            web_name=web_name,
            first_name="A",
            second_name="B",
            team="T",
            aliases=[],
        ),
        gameweek=6,
        deadline_at=T0,
        window_start=T0,
        window_end=T0,
        window_rule="alerts",
        previous_last_match_end=None,
        claimed=[
            ClaimedPost(x_id=x, created_at=T0 + timedelta(hours=h), stale=s) for x, h, s in claims
        ],
        judgements=list(judgements),
    )


def test_counts_of_initials_stale_claims_and_stale_anchors():
    queries = [
        pq("a", "B.Fernandes", [(1, 0, True), (2, 5, False)]),  # anchor 2: fresh
        pq("b", "Saka", [(3, 0, False), (4, 5, True)]),  # anchor 4: stale
        pq("c", "J.Timber", [(5, 3, True), (6, 3, False)]),  # tie on time: x_id 6, fresh
        pq("d", "Isak", []),
    ]
    labels = {"a": {1: True, 2: False}, "b": {3: True, 9: True}, "c": {}, "d": {7: False}}
    counts = player_counts(queries, labels)
    assert counts.queries == 4
    assert counts.with_initial == 2
    assert counts.stale_claims == 3
    assert counts.stale_anchors == 1
    # relevant: a {1}, b {3, 9}; none for c and d
    assert counts.no_relevant == 2
    # unclaimed relevant: a {} (1 claimed), b {9}, c {}, d {}
    assert counts.no_unclaimed_relevant == 3


def test_recall_at_depth_counts_unclaimed_relevant_posts_beyond_the_cut():
    ranked = ids(1, 60)
    claimed = {101, 102}
    labels = {x_id: True for x_id in ids(1, 15)} | {101: True, 102: True}
    numbers = query_numbers(ranked, claimed, labels, 20)
    assert numbers.recall_at_cut == pytest.approx(10 / 15)
    assert numbers.recall_at_depth == pytest.approx(15 / 15)


def test_recall_at_depth_depends_on_the_depth():
    ranked = ids(1, 60)
    labels = {30: True}
    assert query_numbers(ranked, set(), labels, 20).recall_at_depth == 0.0
    assert query_numbers(ranked, set(), labels, 50).recall_at_depth == 1.0


def test_recall_at_depth_is_none_without_an_unclaimed_relevant_post_and_stays_out_of_the_mean():
    ranked = ids(1, 30)
    none = query_numbers(ranked, {1}, {1: True}, 20)
    assert none.recall_at_depth is None
    full = query_numbers(ranked, set(), {2: True}, 20)
    assert aggregate_player([none, full]).recall_at_depth == 1.0
    assert aggregate_player([none]).recall_at_depth is None
