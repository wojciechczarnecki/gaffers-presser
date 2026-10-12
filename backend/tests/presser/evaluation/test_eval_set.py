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


def test_split_is_twenty_two_and_twenty_four_with_real_and_synthetic_in_each():
    for split, size, synthetic in (("dev", 22, 17), ("test", 24, 19)):
        in_split = [case for case in CASES if case.split == split]
        assert len(in_split) == size
        assert sum(case.source == "real" for case in in_split) == 5
        assert sum(case.source == "synthetic" for case in in_split) == synthetic


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


def test_the_set_has_46_cases_and_the_rank_cases():
    assert len(CASES) == 46
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
    (just_outside,) = [
        row
        for row in by_tag["drop_just_outside_1m"].facts.overall.rows
        if row.notable and 1_000_000 in row.left and row.overall_rank < 1_050_000
    ]
    assert just_outside.overall_rank > 1_000_000
    flop_case = by_tag["first_flop_after_other"]
    (flop,) = flop_case.facts.flops
    (season_row,) = [r for r in flop_case.facts.season_facts.rows if r.manager == flop.manager]
    assert (flop.nth_of_season, season_row.flop_streak) == (1, 1)
    last = flop_case.previous[-1]
    assert last.gameweek == flop_case.facts.gameweek - 1
    (flop_line,) = [line for line in last.text.splitlines() if "Wtopa kolejki:*" in line]
    assert flop.manager not in flop_line
    (into_1m,) = [row for row in by_tag["entered_1m_not_100k"].facts.overall.rows if row.entered]
    assert into_1m.entered == [1_000_000] and 100_000 < into_1m.overall_rank < 150_000
    (inside,) = [
        row
        for row in by_tag["drop_inside_top_1m"].facts.overall.rows
        if row.notable and row.movement is not None and row.movement < 0
    ]
    assert inside.left == [] and inside.previous_overall_rank < inside.overall_rank < 1_000_000
    worst_case = by_tag["personal_worst_not_league"].facts
    (flop,) = worst_case.flops
    (personal,) = worst_case.season_facts.personal_worsts
    (league,) = worst_case.season_facts.worst_gameweek
    assert personal.manager == flop.manager != league.manager
    assert personal.gameweek_rank == flop.gameweek_rank < league.gameweek_rank
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


def _decisive(facts, identifier):
    return [
        (moment, entry)
        for moment in facts.key_moments
        for entry in moment.breakdown or []
        if entry.identifier == identifier and entry.decisive
    ]


def test_full_stats_cases_show_what_their_tag_says():
    winner_bonus = _decisive(_synthetic("captain_bonus_decides_winner"), "bonus")
    assert any(m.pair == "winner" and m.kind == "player" for m, _ in winner_bonus)
    yellow = _decisive(_synthetic("yellow_card_decides_pair"), "yellow_cards")
    assert any(m.margin <= 2 for m, _ in yellow)
    assert _decisive(_synthetic("defcon_decides_pair"), "defensive_contribution")
    flipped = _synthetic("bonus_missed_flips_swap").key_moments
    assert any(m.pair == "rivalry" and m.kind == "bonus_missed_by_one" for m in flipped)
    assert any("hat_trick" in i.kinds for i in _synthetic("hat_trick").pitch.items)
    walls = [
        i for i in _synthetic("differential_keeper_wall").pitch.items if "keeper_wall" in i.kinds
    ]
    assert any(i.global_differential or i.league_differential for i in walls)
    assert any(p.big_miss for p in _synthetic("captain_big_miss").captaincy.picks)
    assert any(i.goal_from_nothing for i in _synthetic("goal_from_nothing").pitch.items)
    whole = _synthetic("stat_swing_not_whole").key_moments
    assert any(
        entry.decisive and entry.swing < moment.swing
        for moment in whole
        for entry in moment.breakdown or []
    )
    kept = _synthetic("kept_clean_sheet_against_xgc").key_moments
    assert kept[0].kind == "clean_sheet_kept" and kept[0].against_xgc is True
    assert len(kept[0].players) >= 2
    lost = _synthetic("lost_clean_sheet_xgc").key_moments
    assert any(m.kind == "clean_sheet_lost" and m.expected_goals_conceded is not None for m in lost)
    facts = _synthetic("double_gameweek_bps")
    missed = {m.players[0] for m in facts.key_moments if m.kind == "bonus_missed_by_one"}
    with_bonus = {
        holder.player
        for holder in [*facts.captaincy.picks, *facts.pitch.items]
        if any(e.identifier == "bonus" for e in holder.breakdown or [])
    }
    assert missed and missed <= with_bonus


def test_no_match_stats_case_has_no_new_facts():
    facts = _synthetic("no_match_stats")
    holders = [*facts.captaincy.picks, *facts.pitch.items]
    assert holders
    for holder in holders:
        assert (holder.breakdown, holder.expected_goals, holder.expected_assists) == (
            None,
            None,
            None,
        )
        assert (holder.big_miss, holder.goal_from_nothing) == (None, None)
    kinds = {kind for item in facts.pitch.items for kind in item.kinds}
    assert not kinds & {"hat_trick", "keeper_wall"}
    for moment in facts.key_moments:
        assert moment.kind != "bonus_missed_by_one"
        assert moment.breakdown is None and moment.expected_goals_conceded is None
