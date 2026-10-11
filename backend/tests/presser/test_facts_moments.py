from decimal import Decimal

import pytest

from app.fpl.models import Player
from app.presser.facts import build_fact_sheet, check_fact_sheet
from app.presser.facts.load import MemberRow
from app.presser.facts.matches import FixtureFact
from app.presser.facts.moments import build_key_moments
from app.presser.facts.schema import Table
from tests.presser.helpers import LEAGUE_ID, SEASON, World, live, stats


def sheet(world: World, gameweek: int = 5):
    with world.session() as session:
        return build_fact_sheet(session, SEASON, LEAGUE_ID, gameweek, {})


@pytest.fixture
def world(db):
    world = World(db)
    world.club(2, "Klub Drugi")
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name)
    return world


def player(
    world, player_id, name, points, position=3, club=1, explain=None, minutes=90, live_stats=None
):
    world.player(player_id, name, position=position, club=club)
    world.result(player_id, 5, minutes, points, explain=explain, live=live_stats)


def pick(world, entry_id, points, starters, captain=None):
    world.gw(entry_id, 5, points)
    world.squad(entry_id, 5, starters, captain=captain)


def moments(world):
    return sheet(world).key_moments


def test_winner_pair_differential_haul(world):
    player(world, 1, "Haulman", 14)
    player(world, 2, "Filler", 3)
    pick(world, 10, 60, [1, 2])
    pick(world, 11, 51, [2])
    pick(world, 12, 40, [2])
    (moment,) = moments(world)
    assert (moment.pair, moment.ahead, moment.behind, moment.margin) == (
        "winner",
        "Anna",
        "Bartek",
        9,
    )
    assert (moment.kind, moment.players, moment.swing) == ("player", ["Haulman"], 14)
    assert (moment.ahead_points, moment.behind_points) == (14, 0)


def test_swing_equal_margin_listed_below_not(world):
    player(world, 1, "Nine", 9)
    player(world, 2, "Eight", 8)
    pick(world, 10, 60, [1, 2])
    pick(world, 11, 51, [])
    pick(world, 12, 40, [])
    assert [m.players for m in moments(world)] == [["Nine"]]


def test_captain_multiplier_in_swing(world):
    player(world, 1, "Captained", 14)
    pick(world, 10, 70, [1], captain=1)
    pick(world, 11, 60, [1])
    pick(world, 12, 40, [])
    (moment,) = moments(world)
    assert (moment.ahead_points, moment.behind_points, moment.swing) == (28, 14, 14)


def test_lost_clean_sheet_groups_club_defenders_and_goalkeeper(world):
    world.fixture(501, 5, 2, 1, 0, 2)
    lost = [stats(501, minutes=(90, 2))]
    player(world, 1, "Keeper", 2, position=1, club=2, explain=lost)
    player(world, 2, "Left", 2, position=2, club=2, explain=lost)
    player(world, 3, "Right", 2, position=2, club=2, explain=lost)
    player(world, 4, "Other", 2, position=2, club=1)
    pick(world, 10, 60, [4])
    pick(world, 11, 50, [1, 2, 3])
    pick(world, 12, 40, [4])
    clean = [m for m in moments(world) if m.kind == "clean_sheet_lost"]
    (moment,) = clean
    assert moment.club == "Klub Drugi"
    assert moment.players == ["Keeper", "Left", "Right"]
    assert (moment.ahead_points, moment.behind_points, moment.swing) == (0, -12, 12)


def test_clean_sheet_lost_players_both_hold_alike_are_left_out(world):
    world.fixture(501, 5, 2, 1, 0, 2)
    lost = [stats(501, minutes=(90, 2))]
    player(world, 1, "Keeper", 2, position=1, club=2, explain=lost)
    player(world, 2, "Left", 2, position=2, club=2, explain=lost)
    pick(world, 10, 54, [1])
    pick(world, 11, 50, [1, 2])
    pick(world, 12, 40, [])
    (moment,) = [m for m in moments(world) if m.kind == "clean_sheet_lost"]
    assert (moment.players, moment.swing) == (["Left"], 4)


def test_red_card_of_the_trailing_manager_is_a_moment(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    player(
        world,
        1,
        "Sent",
        -1,
        explain=[stats(501, minutes=(40, 1), red_cards=(1, -3), yellow_cards=(1, -1))],
    )
    pick(world, 10, 60, [])
    pick(world, 11, 57, [1])
    pick(world, 12, 40, [])
    (moment,) = [m for m in moments(world) if m.kind == "red_card"]
    assert (moment.players, moment.swing, moment.margin) == (["Sent"], 3, 3)


def test_cap_five_and_order(world):
    ids = range(1, 8)
    for player_id in ids:
        player(world, player_id, f"P{player_id}", 9 + player_id)
    pick(world, 10, 60, list(ids))
    pick(world, 11, 59, [])
    pick(world, 12, 40, [])
    result = moments(world)
    assert [m.players[0] for m in result] == ["P7", "P6", "P5", "P4", "P3"]
    assert len({m.swing for m in result}) == 5


@pytest.fixture
def race(db):
    world = World(db, gameweeks=6, finished=6)
    for entry_id, name in [
        (10, "Anna Alfa"),
        (11, "Bartek Beta"),
        (12, "Cezary Ceta"),
        (13, "Dawid Delta"),
        (14, "Ewa Epsilon"),
        (15, "Filip Fi"),
    ]:
        world.manager(entry_id, name)
    scores = {12: 90, 13: 80, 14: 70, 10: 60, 11: 58, 15: 1}
    for number in (1, 2, 3):
        for entry_id, points in scores.items():
            world.gw(entry_id, number, points)
    return world


def test_moments_for_rivalry_swap_and_winner_pair_order(race):
    race.player(1, "Winner")
    race.result(1, 4, 90, 12)
    race.player(2, "Climber")
    race.result(2, 4, 90, 20)
    for entry_id, value in {12: 90, 13: 80, 14: 70, 10: 40, 11: 60, 15: 1}.items():
        race.gw(entry_id, 4, value)
        race.squad(entry_id, 4, {12: [1], 11: [2]}.get(entry_id, []))
    result = sheet(race, 4)
    assert [(m.pair, m.ahead, m.behind, m.players[0], m.swing) for m in result.key_moments] == [
        ("winner", "Cezary", "Dawid", "Winner", 12),
        ("rivalry", "Bartek", "Anna", "Climber", 20),
    ]
    assert result.key_moments[1].margin == 14
    assert check_fact_sheet(result) == []


def test_moments_for_podium_change(race):
    race.player(2, "Climber")
    race.result(2, 4, 90, 25)
    for entry_id, value in {12: 50, 13: 50, 14: 49, 10: 100, 11: 40, 15: 1}.items():
        race.gw(entry_id, 4, value)
        race.squad(entry_id, 4, [2] if entry_id == 10 else [])
    result = sheet(race, 4)
    assert [(c.kind, c.manager, c.other, c.gap) for c in result.table.podium_changes] == [
        ("entered", "Anna", "Ewa", 21),
        ("left", "Ewa", None, None),
    ]
    assert [(m.pair, m.ahead, m.behind, m.margin, m.swing) for m in result.key_moments] == [
        ("podium", "Anna", "Ewa", 21, 25)
    ]
    assert check_fact_sheet(result) == []


def test_key_moment_player_ranked_first_on_pitch(world):
    player(world, 1, "InMoment", 12)
    player(world, 2, "Bigger", 15)
    pick(world, 10, 60, [1])
    pick(world, 11, 51, [2])
    pick(world, 12, 40, [])
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == ["InMoment", "Bigger"]
    assert [m.players for m in result.key_moments] == [["InMoment"]]


def test_lost_clean_sheet_alone_keeps_the_pitch_section(world):
    world.fixture(501, 5, 2, 1, 0, 2)
    player(world, 1, "Keeper", 3, position=1, club=2, explain=[stats(501, minutes=(90, 2))])
    pick(world, 10, 60, [])
    pick(world, 11, 56, [1])
    pick(world, 12, 40, [])
    result = sheet(world)
    assert result.pitch.items == []
    assert [(m.kind, m.players, m.swing) for m in result.key_moments] == [
        ("clean_sheet_lost", ["Keeper"], 4)
    ]
    assert "pitch" not in result.empty_sections


def test_no_pitch_items_and_no_moments_leave_the_pitch_section_empty(world):
    player(world, 1, "Filler", 5)
    pick(world, 10, 60, [1])
    pick(world, 11, 51, [1])
    pick(world, 12, 40, [])
    result = sheet(world)
    assert (result.pitch.items, result.key_moments) == ([], [])
    assert "pitch" in result.empty_sections


def test_captained_defender_doubles_the_lost_clean_sheet(world):
    world.fixture(501, 5, 2, 1, 0, 2)
    lost = [stats(501, minutes=(90, 2))]
    player(world, 1, "Captain", 2, position=2, club=2, explain=lost)
    pick(world, 10, 60, [])
    pick(world, 11, 53, [1], captain=1)
    pick(world, 12, 40, [])
    (moment,) = [m for m in moments(world) if m.kind == "clean_sheet_lost"]
    assert (moment.ahead_points, moment.behind_points, moment.swing, moment.margin) == (
        0,
        -8,
        8,
        7,
    )


def test_penalty_save_is_a_moment(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    saved = [stats(501, minutes=(90, 2), clean_sheets=(1, 4), penalties_saved=(1, 5))]
    player(world, 1, "Saver", 11, position=1, explain=saved)
    pick(world, 10, 60, [1])
    pick(world, 11, 55, [])
    pick(world, 12, 40, [])
    result = moments(world)
    assert [(m.kind, m.players, m.swing) for m in result] == [
        ("player", ["Saver"], 11),
        ("penalty_saved", ["Saver"], 5),
    ]


def test_event_of_a_player_without_a_player_row_is_skipped():
    rows = [
        MemberRow(entry_id, 5, points, 0, 0, None, points, 0)
        for entry_id, points in [(10, 60), (11, 58)]
    ]
    result = build_key_moments(
        rows,
        {10: "Anna", 11: "Bartek"},
        Table(),
        {10: {}, 11: {9: 1}},
        {},
        {},
        {},
        [FixtureFact(9, 501, "red_card", 1, -3)],
    )
    assert result == []


@pytest.fixture
def duel(db):
    world = World(db, gameweeks=2, finished=2)
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name)
    return world


def test_podium_story_of_the_winner_pair_keeps_its_own_margin(duel):
    for entry_id, value in {10: 50, 11: 57, 12: 0}.items():
        duel.gw(entry_id, 1, value)
    duel.player(1, "Twelve")
    duel.result(1, 2, 90, 12)
    duel.player(2, "Six")
    duel.result(2, 2, 90, 6)
    for entry_id, value in {10: 80, 11: 70, 12: 10}.items():
        duel.gw(entry_id, 2, value)
        duel.squad(entry_id, 2, [1, 2] if entry_id == 10 else [])
    result = sheet(duel, 2)
    assert [(c.kind, c.manager, c.other, c.gap) for c in result.table.podium_changes] == [
        ("new_leader", "Anna", "Bartek", 3)
    ]
    assert [(m.pair, m.margin, m.players, m.swing) for m in result.key_moments] == [
        ("winner", 10, ["Twelve"], 12),
        ("podium", 3, ["Six"], 6),
    ]
    assert check_fact_sheet(result) == []


def test_podium_change_level_on_points_has_no_moments(duel):
    for entry_id, value in {10: 50, 11: 60, 12: 0}.items():
        duel.gw(entry_id, 1, value)
    duel.player(1, "Five")
    duel.result(1, 2, 90, 5)
    for entry_id, value in {10: 60, 11: 50, 12: 0}.items():
        duel.gw(entry_id, 2, value)
        duel.squad(entry_id, 2, [1] if entry_id == 10 else [])
    result = sheet(duel, 2)
    assert [(c.kind, c.manager, c.other, c.gap) for c in result.table.podium_changes] == [
        ("new_leader", "Anna", "Bartek", 0)
    ]
    assert result.key_moments == []


def test_podium_moments_before_rivalry_moments(race):
    race.player(1, "Leader")
    race.result(1, 4, 90, 22)
    race.player(2, "Riser")
    race.result(2, 4, 90, 25)
    for entry_id, value in {12: 0, 13: 50, 14: 0, 10: 0, 11: 20, 15: 0}.items():
        race.gw(entry_id, 4, value)
        race.squad(entry_id, 4, {13: [1], 11: [2]}.get(entry_id, []))
    result = sheet(race, 4)
    assert [(m.pair, m.ahead, m.behind, m.players[0], m.swing) for m in result.key_moments] == [
        ("podium", "Dawid", "Cezary", "Leader", 22),
        ("rivalry", "Bartek", "Anna", "Riser", 25),
    ]
    assert check_fact_sheet(result) == []


def entries(moment):
    return {e.identifier: e for e in moment.breakdown}


def test_player_moment_carries_breakdown_and_multipliers(world):
    explain = [stats(501, minutes=(90, 2), goals_scored=(2, 10), bonus=(2, 2))]
    player(world, 1, "Haulman", 14, explain=explain, live_stats=live(goals=2))
    pick(world, 10, 70, [1], captain=1)
    pick(world, 11, 60, [1])
    pick(world, 12, 40, [])
    (moment,) = moments(world)
    assert (moment.ahead_multiplier, moment.behind_multiplier) == (2, 1)
    assert (moment.ahead_points, moment.behind_points, moment.swing) == (28, 14, 14)
    assert sum(e.points for e in moment.breakdown) == 14
    assert [(e.identifier, e.value, e.points, e.swing, e.decisive) for e in moment.breakdown] == [
        ("goals_scored", 2, 10, 10, True),
        ("bonus", 2, 2, 2, False),
        ("minutes", 90, 2, 2, False),
    ]


@pytest.mark.parametrize(("margin", "decisive"), [(3, True), (4, False), (5, False)])
def test_breakdown_swing_and_decisive_boundary(world, margin, decisive):
    explain = [stats(501, minutes=(90, 2), bonus=(3, 3))]
    player(world, 1, "Captain", 5, explain=explain, live_stats=live())
    pick(world, 10, 60, [1], captain=1)
    pick(world, 11, 60 - margin, [1])
    pick(world, 12, 10, [])
    (moment,) = moments(world)
    assert (moment.margin, moment.swing) == (margin, 5)
    assert entries(moment)["bonus"].swing == 3
    assert entries(moment)["bonus"].decisive is decisive


def test_trailing_captains_yellow_card_decides_at_margin_1(world):
    explain = [stats(501, minutes=(90, 2), yellow_cards=(1, -1))]
    player(world, 1, "Booked", 1, explain=explain, live_stats=live())
    pick(world, 10, 60, [])
    pick(world, 11, 59, [1], captain=1)
    pick(world, 12, 10, [])
    (moment,) = moments(world)
    assert (moment.pair, moment.ahead, moment.behind, moment.margin) == (
        "winner",
        "Anna",
        "Bartek",
        1,
    )
    assert (moment.kind, moment.players, moment.swing) == ("player", ["Booked"], -2)
    assert (moment.ahead_multiplier, moment.behind_multiplier) == (0, 2)
    assert entries(moment)["yellow_cards"].swing == 2
    assert entries(moment)["yellow_cards"].decisive is True
    assert entries(moment)["minutes"].swing == -4
    assert entries(moment)["minutes"].decisive is False


def test_red_card_entry_never_decisive(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    explain = [stats(501, minutes=(40, 1), red_cards=(1, -3))]
    player(world, 1, "Sent", -2, explain=explain, live_stats=live())
    pick(world, 10, 60, [])
    pick(world, 11, 58, [1])
    pick(world, 12, 10, [])
    result = moments(world)
    (player_moment,) = [m for m in result if m.kind == "player"]
    assert entries(player_moment)["red_cards"].swing == 3
    assert entries(player_moment)["red_cards"].decisive is False
    assert [m.kind for m in result if m.kind == "red_card"] == ["red_card"]


def test_decisive_moment_ranks_by_its_decisive_swing(world):
    booked = [stats(501, minutes=(90, 2), yellow_cards=(1, -1))]
    zed = [stats(501, minutes=(90, 2), bonus=(1, 1))]
    player(world, 1, "Apple", 1, explain=booked, live_stats=live())
    player(world, 2, "Wee", 2, explain=[stats(501, minutes=(90, 2))], live_stats=live())
    player(world, 3, "Zed", 3, explain=zed, live_stats=live())
    pick(world, 10, 60, [2, 3])
    pick(world, 11, 58, [1], captain=1)
    pick(world, 12, 10, [])
    assert [m.players[0] for m in moments(world)] == ["Zed", "Apple", "Wee"]


def test_player_moment_without_live_stats_has_no_breakdown(world):
    explain = [stats(501, minutes=(90, 2), yellow_cards=(1, -1))]
    player(world, 1, "Booked", 1, explain=explain)
    player(world, 2, "Haulman", 14)
    pick(world, 10, 60, [2])
    pick(world, 11, 59, [1], captain=1)
    pick(world, 12, 10, [])
    (moment,) = moments(world)
    assert moment.players == ["Haulman"]
    assert moment.breakdown is None
    assert (moment.ahead_multiplier, moment.behind_multiplier) == (1, 0)


def bonus_fixture(world, fixture_id, rows, near=None):
    """rows: (player id, bps, bonus) of the bonus recipients; near: (player id, bps)."""
    for player_id, bps, bonus in rows:
        with world.session() as session:
            known = session.get(Player, (SEASON, player_id)) is not None
        if not known:
            world.player(player_id, f"Filler{player_id}")
        world.fixture_stat(fixture_id, player_id, "bps", bps)
        world.fixture_stat(fixture_id, player_id, "bonus", bonus)
    if near is not None:
        world.fixture_stat(fixture_id, near[0], "bps", near[1])


NEAR_EXPLAIN = [stats(501, minutes=(90, 2))]


def near_miss(world, bps, bonuses=((20, 30, 3), (21, 28, 2), (22, 25, 1))):
    world.fixture(501, 5, 1, 2, 0, 0)
    player(world, 1, "Near", 2, explain=NEAR_EXPLAIN, live_stats=live())
    bonus_fixture(world, 501, bonuses, near=(1, bps))


def missed(world):
    return [m for m in moments(world) if m.kind == "bonus_missed_by_one"]


def test_bonus_missed_by_one_listed(world):
    near_miss(world, 24)
    pick(world, 10, 60, [])
    pick(world, 11, 59, [1])
    pick(world, 12, 10, [])
    (moment,) = missed(world)
    assert (moment.pair, moment.ahead, moment.behind, moment.margin) == (
        "winner",
        "Anna",
        "Bartek",
        1,
    )
    assert (moment.players, moment.club, moment.missed_bonus) == (["Near"], "Synthetic FC", 1)
    assert (moment.ahead_points, moment.behind_points, moment.swing) == (0, 1, 1)
    assert (moment.ahead_multiplier, moment.behind_multiplier) == (0, 1)
    assert moment.breakdown is None


def test_bonus_missed_by_two_not_listed(world):
    near_miss(world, 23)
    pick(world, 10, 60, [])
    pick(world, 11, 59, [1])
    pick(world, 12, 10, [])
    assert missed(world) == []


def test_bonus_missed_by_leading_managers_player_not_listed(world):
    near_miss(world, 24)
    pick(world, 10, 60, [1])
    pick(world, 11, 59, [])
    pick(world, 12, 10, [])
    assert missed(world) == []


def test_bonus_missed_below_margin_not_listed(world):
    near_miss(world, 24)
    pick(world, 10, 60, [])
    pick(world, 11, 58, [1])
    pick(world, 12, 10, [])
    assert missed(world) == []


def test_bonus_missed_as_captain_reaches_the_margin(world):
    near_miss(world, 24)
    pick(world, 10, 60, [])
    pick(world, 11, 58, [1], captain=1)
    pick(world, 12, 10, [])
    (moment,) = missed(world)
    assert (moment.margin, moment.behind_points, moment.swing) == (2, 2, 2)


def test_shared_lowest_bonus_place_gives_the_shared_bonus(world):
    near_miss(world, 27, bonuses=((20, 30, 3), (21, 28, 2), (22, 28, 2)))
    pick(world, 10, 60, [])
    pick(world, 11, 58, [1])
    pick(world, 12, 10, [])
    (moment,) = missed(world)
    assert (moment.missed_bonus, moment.swing) == (2, 2)


def test_bonus_missed_by_a_player_both_managers_count_uses_the_difference(world):
    near_miss(world, 24)
    pick(world, 10, 60, [1])
    pick(world, 11, 58, [1], captain=1)
    pick(world, 12, 10, [])
    assert missed(world) == []


def test_bonus_in_the_other_fixture_of_a_double_gameweek(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    world.fixture(502, 5, 2, 1, 0, 0)
    explain = [stats(501, minutes=(90, 2), bonus=(1, 1)), stats(502, minutes=(90, 2))]
    player(world, 1, "Near", 5, explain=explain, live_stats=live())
    bonus_fixture(world, 501, [(20, 30, 3), (21, 28, 2), (1, 25, 1)])
    bonus_fixture(world, 502, [(22, 30, 3), (23, 28, 2), (24, 25, 1)], near=(1, 24))
    pick(world, 10, 60, [])
    pick(world, 11, 59, [1])
    pick(world, 12, 10, [])
    (moment,) = missed(world)
    assert (moment.players, moment.missed_bonus) == (["Near"], 1)


def test_fixture_without_bonus_rows_gives_no_near_miss(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    player(world, 1, "Near", 2, explain=NEAR_EXPLAIN, live_stats=live())
    world.fixture_stat(501, 1, "bps", 24)
    pick(world, 10, 60, [])
    pick(world, 11, 59, [1])
    pick(world, 12, 10, [])
    assert missed(world) == []


def lost_sheet(world, keeper_live, defender_live, double=False):
    world.fixture(501, 5, 2, 1, 0, 2)
    if double:
        world.fixture(502, 5, 1, 2, 1, 1)
    lost = [stats(501, minutes=(90, 2))]
    player(world, 1, "Keeper", 2, position=1, club=2, explain=lost, live_stats=keeper_live)
    player(
        world,
        2,
        "Left",
        2,
        position=2,
        club=2,
        explain=[stats(501, minutes=(70, 2))],
        minutes=70,
        live_stats=defender_live,
    )
    pick(world, 10, 60, [])
    pick(world, 11, 57, [1, 2])
    pick(world, 12, 40, [])
    (moment,) = [m for m in moments(world) if m.kind == "clean_sheet_lost"]
    return moment


def test_lost_clean_sheet_carries_xgc_of_most_minutes(world):
    moment = lost_sheet(world, live(xgc="1.40"), live(xgc="1.10"))
    assert moment.expected_goals_conceded == Decimal("1.40")
    assert (moment.ahead_multiplier, moment.breakdown) == (None, None)


def test_lost_clean_sheet_xgc_null_in_double_gameweek(world):
    moment = lost_sheet(world, live(xgc="1.40"), live(xgc="1.10"), double=True)
    assert moment.expected_goals_conceded is None


def test_lost_clean_sheet_xgc_null_without_stats(world):
    assert lost_sheet(world, None, None).expected_goals_conceded is None


def test_lost_clean_sheet_xgc_comes_from_a_player_with_stats(world):
    moment = lost_sheet(world, None, live(xgc="1.10"))
    assert moment.expected_goals_conceded == Decimal("1.10")
