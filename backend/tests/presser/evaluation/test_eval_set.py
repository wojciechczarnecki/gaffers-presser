import re

from app.presser.evaluation.building import _manager_names as manager_names
from app.presser.evaluation.building import load_pseudonyms
from app.presser.evaluation.cases import (
    DEFAULT_CASES_PATH,
    EDGE_TAGS,
    HISTORY_PATH,
    PSEUDONYMS_PATH,
    composition_problems,
    load_cases,
    load_history,
)
from app.presser.facts import check_fact_sheet

CASES = load_cases(DEFAULT_CASES_PATH)
PSEUDONYMS = load_pseudonyms(PSEUDONYMS_PATH)


def test_the_committed_set_has_no_composition_problems():
    assert composition_problems(CASES) == []


def test_split_is_eight_and_eight_with_real_and_synthetic_in_each():
    for split in ("dev", "test"):
        in_split = [case for case in CASES if case.split == split]
        assert len(in_split) == 8
        assert sum(case.source == "real" for case in in_split) == 5
        assert sum(case.source == "synthetic" for case in in_split) == 3


def test_every_sheet_validates():
    assert all(check_fact_sheet(case.facts) == [] for case in CASES)


def test_every_name_comes_from_the_pseudonym_pool():
    pool = set(PSEUDONYMS.managers)
    for case in CASES:
        assert case.facts.league in PSEUDONYMS.leagues, case.id
        assert manager_names(case.facts) <= pool, case.id


def test_edge_cases_cover_every_tag():
    tags = {tag for case in CASES for tag in case.tags}
    assert set(EDGE_TAGS) <= tags


def test_no_case_text_holds_an_entry_or_league_id():
    for line in DEFAULT_CASES_PATH.read_text(encoding="utf-8").splitlines():
        assert not re.search(r"\d{6,}", line)
    assert not re.search(r"\d{6,}", HISTORY_PATH.read_text(encoding="utf-8"))
    assert not re.search(r"\d{6,}", PSEUDONYMS_PATH.read_text(encoding="utf-8"))


def test_history_matches_the_previous_texts_of_the_real_cases():
    history = {(e.league, e.gameweek): e.text for e in load_history()}
    for case in CASES:
        if case.source != "real":
            continue
        alias = case.id.split("-")[1]
        for entry in case.previous:
            assert history[(alias, entry.gameweek)] == entry.text
        wanted = [g for g in (case.facts.gameweek - 2, case.facts.gameweek - 1) if g >= 1]
        assert [entry.gameweek for entry in case.previous] == wanted, case.id


def test_synthetic_cases_after_the_first_gameweek_have_a_previous_presser():
    for case in CASES:
        if case.source == "synthetic" and case.facts.gameweek > 1:
            assert case.previous, case.id
