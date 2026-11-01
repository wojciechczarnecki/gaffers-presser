from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from app.corroboration.rules import (
    GradeRules,
    account_of,
    choose_anchor,
    count_accounts,
    counted_account,
    flag_at,
    freshness,
    grade,
    label_claim,
    newer_contradiction,
    reversed_claim,
)
from app.corroboration.schemas import Claim, FlagState, LabelledPost, PostRef

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
TYPES = ("out", "doubt", "benched", "confirmed_starter", "available")


def _post(x_id, author="a", minutes=0, repost_of=None, quotes=None) -> PostRef:
    return PostRef(
        x_id=x_id,
        author_handle=author,
        reposted_author_handle=repost_of,
        is_repost=repost_of is not None,
        created_at=T0 + timedelta(minutes=minutes),
        text=f"post {x_id}",
        quoted_author_handle=quotes,
    )


def _lab(
    x_id, label, author="a", minutes=0, repost_of=None, origin="sql", quotes=None
) -> LabelledPost:
    return LabelledPost(
        post=_post(x_id, author, minutes, repost_of, quotes),
        label=label,
        origin=origin,
        certainty="likely",
    )


ANCHOR = _post(100, "anchor_account", minutes=0)


S, C, R = "supports", "contradicts", "related"
# anchor type -> claim type -> label, written out so the table is not derived like the code
LABEL_TABLE = {
    "out": {"out": S, "doubt": R, "benched": C, "confirmed_starter": C, "available": C},
    "doubt": {"out": R, "doubt": S, "benched": R, "confirmed_starter": C, "available": C},
    "benched": {"out": C, "doubt": R, "benched": S, "confirmed_starter": C, "available": R},
    "confirmed_starter": {
        "out": C,
        "doubt": C,
        "benched": C,
        "confirmed_starter": S,
        "available": R,
    },
    "available": {"out": C, "doubt": C, "benched": R, "confirmed_starter": R, "available": S},
}


def test_label_table_covers_every_pair_of_the_five_types():
    assert set(LABEL_TABLE) == set(TYPES)
    assert all(set(row) == set(TYPES) for row in LABEL_TABLE.values())


@pytest.mark.parametrize(("anchor_type", "claim_type"), list(product(TYPES, TYPES)))
def test_label_table(anchor_type, claim_type):
    assert label_claim(anchor_type, claim_type) == LABEL_TABLE[anchor_type][claim_type]


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


def test_accounts_the_anchor_accounts_own_older_post_counts_on_neither_side():
    counts = count_accounts([_lab(1, "contradicts", "anchor_account", -30)], ANCHOR)
    assert counts.contradicting == []
    assert newer_contradiction(counts.contradicting, ANCHOR) is False
    result = grade(
        "confirmed", _supporters(1), counts.contradicting, False, T0 - timedelta(hours=1)
    )
    assert result.level == "high"


def test_accounts_unrelated_posts_do_not_count_or_hide_earlier_ones():
    counts = count_accounts(
        [_lab(1, "supports", "rep", -20), _lab(2, "unrelated", "rep", -10)], ANCHOR
    )
    assert [c.post.x_id for c in counts.supporting] == [1]


def test_freshness_strictly_after_new_since_is_new_and_equal_is_context():
    assert freshness(T0 + timedelta(seconds=1), T0) == "new"
    assert freshness(T0, T0) == "context"
    assert freshness(T0 - timedelta(seconds=1), T0) == "context"


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


def test_grade_confirmed_alone_is_medium():
    result = _grade("confirmed")
    assert result.level == "medium"
    assert any("confirmed" in reason for reason in result.reasons)


def test_grade_confirmed_with_one_supporting_account_is_high():
    result = _grade("confirmed", _supporters(1))
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
    result = grade("confirmed", _supporters(1), contradicting, False, T0 - timedelta(minutes=30))
    assert result.level == "medium"
    assert any("contradicting account is new" in reason for reason in result.reasons)


def test_grade_an_old_contradicting_account_does_not_lower():
    contradicting = [_lab(20, "contradicts", "c", minutes=-50)]
    result = grade("confirmed", _supporters(1), contradicting, False, T0 - timedelta(minutes=30))
    assert result.level == "high"


def test_grade_a_newer_contradiction_lowers_medium_to_low():
    result = _grade("likely", newer=True)
    assert result.level == "low"
    assert any("newer than the anchor" in reason for reason in result.reasons)


def test_grade_both_lowering_conditions_lower_one_step_only():
    contradicting = [_lab(20, "contradicts", "c", minutes=-10)]
    result = grade("confirmed", _supporters(1), contradicting, True, T0 - timedelta(minutes=30))
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
    rules = GradeRules(
        high_min_supporting=3, medium_min_supporting=2, confirmed_high_min_supporting=2
    )
    assert _grade("rumour", _supporters(2), rules=rules).level == "medium"
    assert _grade("rumour", _supporters(1), rules=rules).level == "low"
    assert _grade("confirmed", _supporters(1), rules=rules).level == "medium"
    assert _grade("confirmed", _supporters(2), rules=rules).level == "high"


def test_supporting_quote_counts_as_quoted_author():
    counts = count_accounts(
        [
            _lab(1, "supports", "Outsider", -20),
            _lab(2, "supports", "member", -10, quotes="outsider"),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.supporting] == [2]


def test_related_quote_counts_as_quoted_author():
    counts = count_accounts(
        [
            _lab(1, "related", "outsider", -20),
            _lab(2, "related", "member", -10, quotes="Outsider"),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.related] == [2]
    assert counted_account(_post(3, "member", quotes="Outsider"), "related") == "outsider"


def test_contradicting_quote_counts_as_its_own_author():
    counts = count_accounts(
        [
            _lab(1, "supports", "outsider", -20),
            _lab(2, "contradicts", "member", -10, quotes="outsider"),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.supporting] == [1]
    assert [c.post.x_id for c in counts.contradicting] == [2]
    assert counted_account(_post(2, "Member", quotes="outsider"), "contradicts") == "member"


def test_member_quoting_member_counts_once():
    counts = count_accounts(
        [
            _lab(1, "supports", "member_a", -20),
            _lab(2, "supports", "member_b", -10, quotes="member_a"),
        ],
        ANCHOR,
    )
    assert [c.post.x_id for c in counts.supporting] == [2]


def test_quote_of_the_anchor_author_never_supports():
    counts = count_accounts([_lab(1, "supports", "member", -10, quotes="Anchor_Account")], ANCHOR)
    assert counts.supporting == []


def test_quote_without_stored_quoted_post_counts_as_its_author():
    assert counted_account(_post(1, "Member"), "supports") == "member"
    counts = count_accounts([_lab(1, "supports", "member", -10)], ANCHOR)
    assert [c.post.x_id for c in counts.supporting] == [1]


def test_grade_weighs_a_quote_by_the_quoted_account():
    seen = []

    def credibility(account: str) -> float:
        seen.append(account)
        return 1.0

    grade(
        "rumour",
        [_lab(1, "supports", "member", -10, quotes="Outsider")],
        [],
        False,
        T0,
        GradeRules(credibility=credibility),
    )
    assert seen == ["outsider"]


QUOTING_ANCHOR = _post(100, "member", minutes=0, quotes="Outsider")


def test_quoted_anchor_neither_the_quoted_nor_the_quoting_account_counts():
    counts = count_accounts(
        [
            _lab(1, "supports", "outsider", -20),
            _lab(2, "supports", "Member", -10),
            _lab(3, "supports", "other", -5),
        ],
        QUOTING_ANCHOR,
    )
    assert [c.post.x_id for c in counts.supporting] == [3]


def test_quoted_anchor_its_quoting_account_never_contradicts():
    counts = count_accounts(
        [
            _lab(1, "contradicts", "member", -10),
            _lab(2, "contradicts", "outsider", -5),
        ],
        QUOTING_ANCHOR,
    )
    assert counts.contradicting == []


def _claim(x_id, event_type, minutes, author="a") -> Claim:
    return Claim(post=_post(x_id, author, minutes), event_type=event_type, certainty="likely")


CANDIDATE = _claim(50, "available", 0, "fit_account")


def _flag(status, minutes, news="") -> FlagState:
    return FlagState(status, news, T0 + timedelta(minutes=minutes))


def test_reversed_claim_own_account_counts():
    out = _claim(1, "out", -10, "fit_account")
    assert reversed_claim(CANDIDATE, [out, CANDIDATE]) is out


def test_reversed_claim_another_accounts_older_contradiction():
    doubt = _claim(2, "doubt", -5, "other")
    assert reversed_claim(CANDIDATE, [doubt, CANDIDATE]) is doubt


def test_reversed_claim_a_newer_supporting_claim_ends_it():
    out, available = _claim(1, "out", -10), _claim(2, "available", -5)
    assert reversed_claim(CANDIDATE, [out, available, CANDIDATE]) is None
    out_then_starter = [out, _claim(3, "confirmed_starter", -5)]
    anchor = _claim(4, "out", 0)
    assert reversed_claim(anchor, [*out_then_starter, anchor]) is out_then_starter[1]


def test_reversed_claim_a_newer_contradiction_is_not_a_reversal():
    assert reversed_claim(CANDIDATE, [CANDIDATE, _claim(4, "out", 10)]) is None
    assert reversed_claim(CANDIDATE, [CANDIDATE]) is None


def test_reversed_claim_benched_before_out():
    benched = _claim(1, "benched", -10)
    anchor = _claim(2, "out", 0)
    assert reversed_claim(anchor, [benched, anchor]) is benched
    assert reversed_claim(anchor, [_claim(3, "doubt", -10), anchor]) is None


def test_flag_at_takes_the_latest_row_at_or_before_the_moment():
    flags = [_flag("a", -100), _flag("d", -20, "knock"), _flag("a", 20)]
    assert flag_at(flags, T0) == flags[1]
    assert flag_at(flags, T0 + timedelta(minutes=20)) == flags[2]
    assert flag_at(flags, T0 - timedelta(minutes=200)) is None
    assert flag_at([], T0) is None


def test_flag_at_takes_the_later_of_rows_observed_at_the_same_moment():
    baseline, change = _flag("a", -20), _flag("d", -20, "knock")
    assert flag_at([baseline, change], T0) is change


def test_choose_anchor_available_by_reversed_news():
    out = _claim(1, "out", -10)
    choice = choose_anchor([out, CANDIDATE], [_flag("a", -100)])
    assert choice.anchor is CANDIDATE
    assert choice.reversed_claim is out
    assert choice.reversed_flag is None


def test_choose_anchor_available_by_a_non_a_flag():
    flag = _flag("d", -30, "Hip injury")
    choice = choose_anchor([CANDIDATE], [_flag("a", -100), flag])
    assert choice.anchor is CANDIDATE
    assert choice.reversed_claim is None
    assert choice.reversed_flag is flag
    # the flag is kept only without reversed news
    with_news = choose_anchor([_claim(1, "out", -10), CANDIDATE], [flag])
    assert with_news.reversed_claim is not None and with_news.reversed_flag is None


@pytest.mark.parametrize("older_type", ["confirmed_starter", "benched"])
def test_choose_anchor_available_with_an_a_flag_falls_back_to_a_starter_or_benched(older_type):
    older = _claim(1, older_type, -10)
    choice = choose_anchor([older, CANDIDATE], [_flag("a", -100)])
    assert choice.anchor is older
    assert choice.reversed_claim is None


@pytest.mark.parametrize(
    "types",
    [
        ["out", "available", "available"],
        ["doubt", "available", "available"],
        ["confirmed_starter", "out", "available", "available"],
    ],
)
def test_choose_anchor_non_qualifying_available_never_falls_back_to_out_or_doubt(types):
    claims = [_claim(i + 1, t, -10 * (len(types) - i - 1)) for i, t in enumerate(types)]
    assert choose_anchor(claims, [_flag("a", -100)]) is None


def test_choose_anchor_only_available_and_no_qualifying_flag_is_none():
    assert choose_anchor([CANDIDATE], [_flag("a", -100)]) is None


def test_choose_anchor_available_with_no_flag_row_does_not_qualify():
    assert choose_anchor([CANDIDATE], []) is None


def test_choose_anchor_a_flag_turned_a_before_the_post_is_none():
    assert choose_anchor([CANDIDATE], [_flag("d", -60), _flag("a", -5)]) is None


def test_choose_anchor_available_after_available_needs_the_flag():
    first, second = _claim(1, "available", -10), _claim(2, "available", 0)
    assert choose_anchor([first, second], [_flag("a", -100)]) is None
    flagged = choose_anchor([first, second], [_flag("d", -100)])
    assert flagged.anchor is second and flagged.reversed_flag is not None


def test_choose_anchor_given_non_newest_available_falls_back():
    starter = _claim(1, "confirmed_starter", -20)
    available = _claim(2, "available", -10)
    newest = _claim(3, "benched", 0)
    choice = choose_anchor([starter, available, newest], [_flag("d", -100)], anchor_x_id=2)
    assert choice.anchor is newest
    with_out = [_claim(1, "out", -20), available, _claim(3, "out", 0)]
    assert choose_anchor(with_out, [_flag("d", -100)], anchor_x_id=2) is None


def test_choose_anchor_given_non_available_stays():
    out, doubt = _claim(1, "out", -20), _claim(2, "doubt", -10)
    newest = _claim(3, "out", 0)
    choice = choose_anchor([out, doubt, newest], [], anchor_x_id=1)
    assert choice.anchor is out
    assert choice.reversed_claim is None
    assert choose_anchor([out, doubt, newest], []).anchor is newest
    assert choose_anchor([], []) is None
