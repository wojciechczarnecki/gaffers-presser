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


def test_split_is_fifteen_and_fifteen_with_real_and_synthetic_in_each():
    for split in ("dev", "test"):
        in_split = [case for case in CASES if case.split == split]
        assert len(in_split) == 15
        assert sum(case.source == "real" for case in in_split) == 5
        assert sum(case.source == "synthetic" for case in in_split) == 10


def test_every_sheet_validates():
    assert all(check_fact_sheet(case.facts) == [] for case in CASES)


def test_every_name_comes_from_the_pseudonym_pool():
    pool = set(PSEUDONYMS.managers)
    for case in CASES:
        assert case.facts.league in PSEUDONYMS.leagues, case.id
        assert manager_names(case.facts) <= pool, case.id
        assert {row.team_name for row in case.facts.table.rows} <= set(PSEUDONYMS.teams), case.id


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


def test_the_set_has_30_cases_and_the_rank_cases():
    assert len(CASES) == 30
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


def _synthetic(tag):
    return next(case.facts for case in CASES if case.source == "synthetic" and tag in case.tags)


def test_match_story_cases_show_what_their_tag_says():
    facts = _synthetic("differential_decides_winner")
    assert any(
        m.pair == "winner" and m.kind == "player" and m.swing >= m.margin for m in facts.key_moments
    )
    facts = _synthetic("lost_clean_sheet_moment")
    assert any(m.kind == "clean_sheet_lost" and len(m.players) >= 2 for m in facts.key_moments)
    assert any("template_blank" in i.kinds for i in _synthetic("template_blank").pitch.items)
    kinds = {k for i in _synthetic("red_card_own_goal").pitch.items for k in i.kinds}
    assert {"red_card", "own_goal"} <= kinds
    assert any("lucky_sub" in i.kinds for i in _synthetic("lucky_sub").pitch.items)
    hits = _synthetic("hits_judged").bench_transfers.hits
    assert any(h.paid_off for h in hits) and any(not h.paid_off for h in hits)
    assert any(s.neighbour_gameweeks >= 3 for s in _synthetic("rivalry_swap").table.rivalry_swaps)
    assert any(c.kind == "new_leader" for c in _synthetic("podium_new_leader").table.podium_changes)
    assert "chips" in _synthetic("no_chips").empty_sections
    unknown = _synthetic("ownership_unknown").pitch.items
    assert unknown and all(item.global_differential is None for item in unknown)
