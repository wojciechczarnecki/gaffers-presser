from collections import Counter

from app.corroboration.evaluation.cases import (
    DEFAULT_CASES_PATH,
    LABELS,
    LABELS_NOT_YET_REQUIRED,
    PRELABEL_MODEL,
    composition_problems,
    load_cases,
)
from app.llm.chat import DEFAULT_MODEL


def test_committed_set_composition():
    cases = load_cases(DEFAULT_CASES_PATH)
    assert composition_problems(cases) == []
    assert {case.labelled_by for case in cases} == {PRELABEL_MODEL}
    assert PRELABEL_MODEL != DEFAULT_MODEL


def test_every_required_label_is_present_in_the_test_split_and_the_misses_are_kept():
    cases = load_cases(DEFAULT_CASES_PATH)
    test_labels = Counter(case.expected for case in cases if case.split == "test")
    assert set(LABELS) - LABELS_NOT_YET_REQUIRED <= set(test_labels)
    assert any(not case.has_player_event and case.expected != "unrelated" for case in cases)


def test_the_committed_set_holds_public_post_text_only():
    for case in load_cases(DEFAULT_CASES_PATH):
        assert "@" not in case.player.web_name
        assert case.post.author_handle and case.anchor.author_handle
