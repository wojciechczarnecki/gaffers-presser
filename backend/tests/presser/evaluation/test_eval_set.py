import json
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


def test_split_is_ten_and_ten_with_real_and_synthetic_in_each():
    for split in ("dev", "test"):
        in_split = [case for case in CASES if case.split == split]
        assert len(in_split) == 10
        assert sum(case.source == "real" for case in in_split) == 5
        assert sum(case.source == "synthetic" for case in in_split) == 5


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


RANK_KEYS = {
    "gameweek_rank",
    "overall_rank",
    "previous_overall_rank",
    "movement",
    "entered",
    "left",
}


def _without_rank_values(value):
    if isinstance(value, dict):
        return {k: _without_rank_values(v) for k, v in value.items() if k not in RANK_KEYS}
    if isinstance(value, list):
        return [_without_rank_values(item) for item in value]
    return value


def test_the_set_has_20_cases_and_the_rank_cases():
    assert len(CASES) == 20
    tags = {tag for case in CASES for tag in case.tags}
    assert {"enter_top_10k", "rise_in_top_10k", "drop_out_of_top_1m", "gw_rank_unknown"} <= tags


def test_rank_cases_show_what_their_tag_says():
    by_tag = {tag: case for case in CASES for tag in case.tags if case.source == "synthetic"}
    entered = by_tag["enter_top_10k"].facts.overall.rows
    assert any(10_000 in row.entered for row in entered)
    rising = by_tag["rise_in_top_10k"].facts.overall.rows
    assert any(
        row.notable and row.movement and row.movement > 0 and row.overall_rank <= 10_000
        for row in rising
    )
    dropped = by_tag["drop_out_of_top_1m"].facts.overall.rows
    assert any(1_000_000 in row.left for row in dropped)
    unknown = by_tag["gw_rank_unknown"].facts
    assert all(score.gameweek_rank is None for score in unknown.winners + unknown.flops)
    assert unknown.season_facts.best_gameweek == [] and "overall" in unknown.empty_sections


def test_no_case_text_holds_an_entry_or_league_id():
    for case in CASES:
        stripped = json.dumps(
            _without_rank_values(case.model_dump(mode="json")), ensure_ascii=False
        )
        assert not re.search(r"\d{6,}", stripped), case.id
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
