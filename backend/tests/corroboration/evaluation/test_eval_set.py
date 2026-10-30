from collections import Counter

from app.corroboration.evaluation.cases import PRELABEL_MODEL, V1_CASES_PATH, load_cases


def test_v1_set_is_kept_as_built():
    cases = load_cases(V1_CASES_PATH)
    assert len(cases) == 51
    splits = Counter(case.split for case in cases)
    assert (splits["dev"], splits["test"]) == (15, 36)
    assert {case.labelled_by for case in cases} == {PRELABEL_MODEL}
    assert not any(case.expected == "contradicts" for case in cases)


def test_the_v1_set_holds_public_post_text_only():
    for case in load_cases(V1_CASES_PATH):
        assert "@" not in case.player.web_name
        assert case.post.author_handle and case.anchor.author_handle
