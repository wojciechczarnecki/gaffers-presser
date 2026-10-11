from sqlmodel import select

from app.fpl.models import Manager
from app.presser.facts import build_fact_sheet, check_fact_sheet
from tests.presser.helpers import LEAGUE_ID, SEASON, World, stats

NAMES = ["Anna", "Bartek", "Cezary", "Dawid", "Ewa", "Filip", "Gosia", "Hela", "Irek", "Jola"]
NAMES += ["Kamil", "Lucyna"]


def sheet(world: World):
    with world.session() as session:
        return build_fact_sheet(session, SEASON, LEAGUE_ID, 5, {})


def league(db, managers: int = 8) -> World:
    world = World(db, gameweeks=6, finished=5)
    world.club(2, "Klub Drugi")
    for number in range(managers):
        world.manager(10 + number, f"{NAMES[number]} Test")
    return world


def squads(world: World, held: dict[int, list[int]], captains: dict[int, int] | None = None):
    """`held`: manager entry -> starters; every manager gets a gameweek row once."""
    for entry_id in world_entries(world):
        starters = held.get(entry_id, [])
        world.gw(entry_id, 5, 50)
        world.squad(entry_id, 5, starters, captain=(captains or {}).get(entry_id))


def world_entries(world: World) -> list[int]:
    with world.session() as session:
        return sorted(session.exec(select(Manager.entry_id)))


def players(world: World, points: dict[int, int], names=None):
    for player_id, value in points.items():
        world.player(player_id, (names or {}).get(player_id, f"P{player_id}"))
        world.result(player_id, 5, 90, value)


def item(result, name):
    return next(i for i in result.pitch.items if i.player == name)


def test_differential_haul_boundary_10_and_9(db):
    world = league(db)
    players(world, {1: 10, 2: 9})
    squads(world, {10: [1, 2], 11: [1, 2]})
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == ["P1"]
    assert item(result, "P1").kinds == ["differential_haul"]


def test_global_flag_boundary_9_9_and_10_0(db):
    world = league(db)
    players(world, {1: 10, 2: 10})
    squads(world, {10: [1, 2], 11: [1, 2], 12: [1, 2]})
    world.snapshot(5, 1, "9.9")
    world.snapshot(5, 2, "10.0")
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == ["P1"]
    assert item(result, "P1").global_differential is True
    assert item(result, "P1").league_differential is False


def test_global_flag_null_without_snapshot(db):
    world = league(db)
    players(world, {1: 10})
    squads(world, {10: [1]})
    result = sheet(world)
    assert item(result, "P1").global_differential is None
    assert item(result, "P1").league_differential is True


def test_global_flag_false_when_ownership_is_high_but_league_differential(db):
    world = league(db)
    players(world, {1: 10})
    squads(world, {10: [1]})
    world.snapshot(5, 1, "40.0")
    assert item(sheet(world), "P1").global_differential is False


def test_league_flag_two_managers(db):
    world = league(db)
    players(world, {1: 10, 2: 10})
    squads(world, {10: [1, 2], 11: [1, 2], 12: [2]})
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == ["P1"]
    assert item(result, "P1").league_differential is True


def test_template_boundary_6_of_12_and_5_of_12(db):
    world = league(db, 12)
    players(world, {1: 12, 2: 12, 3: 1, 4: 1})
    held = {10 + n: [1, 3] for n in range(6)} | {16 + n: [2, 4] for n in range(5)}
    squads(world, held)
    world.snapshot(5, 1, "50.0")
    world.snapshot(5, 2, "50.0")
    result = sheet(world)
    assert {i.player: i.kinds for i in result.pitch.items} == {
        "P1": ["template_haul"],
        "P3": ["template_blank"],
    }


def test_template_item_lists_with_benched_and_without(db):
    world = league(db, 12)
    players(world, {1: 12})
    for n in range(6):
        world.gw(10 + n, 5, 50)
        world.squad(10 + n, 5, [1] if n else [], [] if n else [1])
    world.player(2, "Other")
    world.result(2, 5, 90, 5)
    for n in range(6, 12):
        world.gw(10 + n, 5, 50)
        world.squad(10 + n, 5, [2])
    result = sheet(world)
    pitch_item = item(result, "P1")
    assert len(pitch_item.owners) == 5
    assert len(pitch_item.benched) == 1
    assert len(pitch_item.without) == 6
    assert check_fact_sheet(result) == []


def test_item_names_club_points_owners_multiplier(db):
    world = league(db)
    world.player(1, "Haaland", position=4, club=2)
    world.result(1, 5, 90, 14)
    squads(world, {10: [1], 11: [1]}, captains={10: 1})
    pitch_item = item(sheet(world), "Haaland")
    assert (pitch_item.club, pitch_item.points) == ("Klub Drugi", 14)
    assert [(o.manager, o.multiplier) for o in pitch_item.owners] == [("Anna", 2), ("Bartek", 1)]


def test_player_counted_for_nobody_not_listed(db):
    world = league(db, 12)
    world.player(1, "Benched")
    world.result(1, 5, 30, 1, explain=[stats(501, minutes=(30, 1), red_cards=(1, -3))])
    world.fixture(501, 5, 1, 2, 0, 0)
    world.player(2, "Blank")
    world.result(2, 5, 90, 1)
    for n in range(12):
        world.gw(10 + n, 5, 50)
        world.squad(10 + n, 5, [2], [1] if n == 0 else [])
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == ["Blank"]


def test_cap_and_ranking(db):
    world = league(db)
    world.club(3, "Klub Trzeci")
    world.fixture(501, 5, 3, 1, 1, 0)
    ids = list(range(1, 11))
    for player_id in ids:
        world.player(player_id, f"P{player_id:02d}", position=3)
    carded = {
        1: [stats(501, minutes=(90, 2), red_cards=(1, -3))],
        2: [stats(501, minutes=(90, 2), own_goals=(1, -2))],
        3: [stats(501, minutes=(90, 2), penalties_missed=(1, -2))],
    }
    points = {1: 0, 2: 1, 3: 1, 4: 10, 5: 15, 6: 12, 7: 11, 8: 5, 9: 5, 10: 5}
    for player_id in ids:
        world.result(player_id, 5, 90, points[player_id], explain=carded.get(player_id))
    squads(world, {10: ids})
    result = sheet(world)
    assert len(result.pitch.items) == 7
    assert [i.player for i in result.pitch.items] == [
        "P02",
        "P03",
        "P01",
        "P05",
        "P06",
        "P07",
        "P04",
    ]


def test_cap_of_eight_items(db):
    world = league(db)
    ids = list(range(1, 11))
    for player_id in ids:
        world.player(player_id, f"P{player_id:02d}")
        world.result(player_id, 5, 90, 10 + player_id)
    squads(world, {10: ids})
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == [f"P{n:02d}" for n in range(10, 2, -1)]


def test_lucky_sub_ranks_last(db):
    world = league(db)
    world.fixture(501, 5, 1, 2, 0, 1)
    world.player(1, "Lucky", position=2)
    world.result(1, 5, 50, 5, explain=[stats(501, minutes=(50, 1), clean_sheets=(1, 4))])
    world.player(2, "Haul")
    world.result(2, 5, 90, 11)
    squads(world, {10: [1, 2]})
    result = sheet(world)
    assert [i.player for i in result.pitch.items] == ["Haul", "Lucky"]
    assert item(result, "Lucky").events[0].kind == "lucky_sub"


def test_sheet_with_pitch_is_consistent(db):
    world = league(db)
    players(world, {1: 12})
    squads(world, {10: [1]})
    assert check_fact_sheet(sheet(world)) == []
