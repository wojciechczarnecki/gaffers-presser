from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.corroboration.rules import (
    GradeRules,
    account_of,
    count_accounts,
    freshness,
    grade,
    label_claim,
    newer_contradiction,
    reversal,
)
from app.corroboration.schemas import LabelledPost, PostRef

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
TYPES = ("out", "doubt", "benched", "confirmed_starter")


def _post(x_id, author="a", minutes=0, repost_of=None) -> PostRef:
    return PostRef(
        x_id=x_id,
        author_handle=author,
        reposted_author_handle=repost_of,
        is_repost=repost_of is not None,
        created_at=T0 + timedelta(minutes=minutes),
        text=f"post {x_id}",
    )


def _lab(x_id, label, author="a", minutes=0, repost_of=None, origin="sql") -> LabelledPost:
    return LabelledPost(
        post=_post(x_id, author, minutes, repost_of), label=label, origin=origin, certainty="likely"
    )


ANCHOR = _post(100, "anchor_account", minutes=0)


@pytest.mark.parametrize(("anchor_type", "claim_type"), list(product(TYPES, TYPES)))
def test_label_table(anchor_type, claim_type):
    if anchor_type == claim_type:
        expected = "supports"
    elif "confirmed_starter" in (anchor_type, claim_type):
        expected = "contradicts"
    else:
        expected = "related"
    assert label_claim(anchor_type, claim_type) == expected


def test_accounts_a_repost_counts_as_its_original_author():
    assert account_of(_post(1, "lister", repost_of="Origin")) == "origin"
    assert account_of(_post(2, "Lister")) == "lister"


def test_accounts_a_repost_with_a_null_original_falls_back_to_its_author():
    post = PostRef(1, "Lister", None, True, T0, "t")
    assert account_of(post) == "lister"


def test_accounts_are_compared_case_insensitively():
    counts = count_accounts(
        [_lab(1, "supports", "Reporter", -10), _lab(2, "supports", "reporter", -5)], ANCHOR
    )
    assert [c.post.x_id for c in counts.supporting] == [2]


def test_accounts_a_repost_and_its_original_author_count_once():
    counts = count_accounts(
        [
            _lab(1, "supports", "origin", -10),
            _lab(2, "supports", "lister", -5, repost_of="Origin"),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.supporting] == [2]


def test_accounts_the_newest_post_per_account_decides():
    counts = count_accounts(
        [_lab(1, "supports", "rep", -20), _lab(2, "related", "rep", -10)], ANCHOR
    )
    assert counts.supporting == []
    assert [c.post.x_id for c in counts.related] == [2]


def test_accounts_the_anchor_account_never_supports():
    counts = count_accounts(
        [_lab(1, "supports", "Anchor_Account", -10), _lab(2, "supports", "other", -5)], ANCHOR
    )
    assert [c.post.x_id for c in counts.supporting] == [2]


def test_accounts_the_anchor_post_itself_is_left_out():
    counts = count_accounts(
        [
            LabelledPost(ANCHOR, "supports", "sql", "likely", "out"),
            _lab(2, "supports", "other", -5),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.supporting] == [2]


def test_accounts_the_anchor_account_never_contradicts():
    counts = count_accounts(
        [
            _lab(1, "contradicts", "Anchor_Account", -30),
            _lab(2, "contradicts", "anchor_account", 10, origin="judge"),
            _lab(3, "contradicts", "other", -20),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.contradicting] == [3]


def test_accounts_the_anchor_accounts_own_reversal_sets_no_flag_and_no_lowering():
    counts = count_accounts([_lab(1, "contradicts", "anchor_account", -30)], ANCHOR)
    assert counts.contradicting == []
    assert reversal(counts.contradicting, ANCHOR) is False
    assert newer_contradiction(counts.contradicting, ANCHOR) is False
    assert grade("confirmed", [], counts.contradicting, False, T0 - timedelta(hours=1)).level == (
        "high"
    )


def test_accounts_unrelated_posts_do_not_count_or_hide_earlier_ones():
    counts = count_accounts(
        [_lab(1, "supports", "rep", -20), _lab(2, "unrelated", "rep", -10)], ANCHOR
    )
    assert [c.post.x_id for c in counts.supporting] == [1]


def test_freshness_strictly_after_new_since_is_new_and_equal_is_context():
    assert freshness(T0 + timedelta(seconds=1), T0) == "new"
    assert freshness(T0, T0) == "context"
    assert freshness(T0 - timedelta(seconds=1), T0) == "context"


def test_reversal_an_older_contradicting_account_sets_it():
    assert reversal([_lab(1, "contradicts", "x", -1)], ANCHOR) is True


def test_reversal_a_newer_contradiction_alone_does_not():
    assert reversal([_lab(1, "contradicts", "x", 5, origin="judge")], ANCHOR) is False
    assert reversal([], ANCHOR) is False


def test_newer_contradiction_only_judged_posts_newer_than_the_anchor():
    assert newer_contradiction([_lab(1, "contradicts", "x", 5, origin="judge")], ANCHOR) is True
    assert newer_contradiction([_lab(1, "contradicts", "x", 5, origin="sql")], ANCHOR) is False
    assert newer_contradiction([_lab(1, "contradicts", "x", -5, origin="judge")], ANCHOR) is False
    assert newer_contradiction([_lab(1, "contradicts", "x", 0, origin="judge")], ANCHOR) is False


NEW_SINCE = T0 - timedelta(hours=1)


def _supporters(n, minutes=-30):
    return [_lab(10 + i, "supports", f"s{i}", minutes) for i in range(n)]


def _grade(certainty, supporting=(), contradicting=(), newer=False, **kwargs):
    return grade(certainty, supporting, contradicting, newer, NEW_SINCE, **kwargs)


def test_grade_confirmed_is_high():
    result = _grade("confirmed")
    assert result.level == "high"
    assert any("confirmed" in reason for reason in result.reasons)


def test_grade_two_supporting_accounts_is_high():
    result = _grade("rumour", _supporters(2))
    assert result.level == "high"
    assert any("2 independent supporting" in reason for reason in result.reasons)


def test_grade_likely_is_medium():
    result = _grade("likely")
    assert result.level == "medium"
    assert any("likely" in reason for reason in result.reasons)


def test_grade_one_supporting_account_is_medium():
    result = _grade("rumour", _supporters(1))
    assert result.level == "medium"
    assert any("1 independent supporting" in reason for reason in result.reasons)


def test_grade_otherwise_is_low():
    result = _grade("rumour")
    assert result.level == "low"
    assert result.reasons


def test_grade_a_new_contradicting_account_lowers_high_to_medium():
    contradicting = [_lab(20, "contradicts", "c", minutes=-10)]
    result = grade("confirmed", [], contradicting, False, T0 - timedelta(minutes=30))
    assert result.level == "medium"
    assert any("contradicting account is new" in reason for reason in result.reasons)


def test_grade_an_old_contradicting_account_does_not_lower():
    contradicting = [_lab(20, "contradicts", "c", minutes=-50)]
    result = grade("confirmed", [], contradicting, False, T0 - timedelta(minutes=30))
    assert result.level == "high"


def test_grade_a_newer_contradiction_lowers_medium_to_low():
    result = _grade("likely", newer=True)
    assert result.level == "low"
    assert any("newer than the anchor" in reason for reason in result.reasons)


def test_grade_both_lowering_conditions_lower_one_step_only():
    contradicting = [_lab(20, "contradicts", "c", minutes=-10)]
    result = grade("confirmed", [], contradicting, True, T0 - timedelta(minutes=30))
    assert result.level == "medium"
    joined = " ".join(result.reasons)
    assert "contradicting account is new" in joined
    assert "newer than the anchor" in joined


def test_grade_low_stays_low():
    assert _grade("rumour", newer=True).level == "low"


def test_grade_a_custom_credibility_changes_the_supporting_sum():
    supporters = _supporters(2)
    weights = {"s0": 0.25, "s1": 0.25}
    rules = GradeRules(credibility=lambda account: weights.get(account, 1.0))
    assert _grade("rumour", supporters, rules=rules).level == "low"
    rules = GradeRules(credibility=lambda account: 2.0)
    assert _grade("rumour", _supporters(1), rules=rules).level == "high"


def test_grade_thresholds_are_named_parameters():
    rules = GradeRules(high_min_supporting=3, medium_min_supporting=2)
    assert _grade("rumour", _supporters(2), rules=rules).level == "medium"
    assert _grade("rumour", _supporters(1), rules=rules).level == "low"
