import pytest

from app.presser.facts import build_fact_sheet, check_fact_sheet
from tests.presser.helpers import LEAGUE_ID, SEASON, World, stats


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


def player(world, player_id, name, points, position=3, club=1, explain=None, minutes=90):
    world.player(player_id, name, position=position, club=club)
    world.result(player_id, 5, minutes, points, explain=explain)


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
