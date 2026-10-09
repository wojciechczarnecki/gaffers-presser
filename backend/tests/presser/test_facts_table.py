import pytest

from app.presser.facts import build_fact_sheet, check_fact_sheet
from tests.presser.helpers import LEAGUE_ID, SEASON, World


def sheet(world: World, gameweek: int = 5, nicknames=None):
    with world.session() as session:
        return build_fact_sheet(session, SEASON, LEAGUE_ID, gameweek, nicknames or {})


@pytest.fixture
def world(db):
    world = World(db)
    for fpl_id, name in [(1, "Alpha"), (2, "Bravo")]:
        world.player(fpl_id, name)
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name, team_name=f"Realni FC {entry_id}")
    return world


def play(world, gameweek, scores, **kwargs):
    for entry_id, points in scores.items():
        world.gw(entry_id, gameweek, points, **kwargs)


def test_table_from_total_points_with_movement(world):
    play(world, 4, {10: 60, 11: 50, 12: 40})
    play(world, 5, {10: 30, 11: 70, 12: 20})
    table = sheet(world).table
    assert [(r.rank, r.manager, r.total_points, r.movement) for r in table.rows] == [
        (1, "Bartek", 120, 1),
        (2, "Anna", 90, -1),
        (3, "Cezary", 60, 0),
    ]


def test_no_movement_at_first_gameweek(world):
    play(world, 1, {10: 60, 11: 50, 12: 40})
    table = sheet(world, 1).table
    assert [r.movement for r in table.rows] == [None, None, None]
    assert table.climbers == [] and table.fallers == []


def test_movement_none_for_member_without_previous_row(world):
    play(world, 4, {10: 60, 11: 50})
    play(world, 5, {10: 30, 11: 70, 12: 20})
    rows = {r.manager: r.movement for r in sheet(world).table.rows}
    assert rows["Cezary"] is None
    assert rows["Bartek"] == 1


def test_top3_gaps_climber_faller(world):
    world.manager(13, "Dariusz Delta")
    play(world, 4, {10: 60, 11: 50, 12: 40, 13: 30})
    play(world, 5, {10: 10, 11: 20, 12: 25, 13: 70})
    table = sheet(world).table
    assert [(t.manager, t.total_points, t.behind_leader) for t in table.top3] == [
        ("Dariusz", 100, 0),
        ("Anna", 70, 30),
        ("Bartek", 70, 30),
    ]
    assert table.climbers == ["Dariusz"]
    assert table.fallers == ["Anna", "Cezary"]
    assert [(r.rank, r.manager) for r in table.rows] == [
        (1, "Dariusz"),
        (2, "Anna"),
        (2, "Bartek"),
        (4, "Cezary"),
    ]


def test_season_wins_flops_and_streaks(world):
    play(world, 1, {10: 60, 11: 50, 12: 40})
    play(world, 2, {10: 60, 11: 60, 12: 20})
    play(world, 3, {10: 50, 11: 60, 12: 20})
    world.gw(10, 4, 0, has_team=False)
    play(world, 4, {11: 60, 12: 20})
    play(world, 5, {10: 60, 11: 40, 12: 20})
    rows = {r.manager: r for r in sheet(world).season_facts.rows}
    assert (rows["Anna"].wins, rows["Anna"].flops, rows["Anna"].win_streak) == (3, 0, 1)
    assert (rows["Bartek"].wins, rows["Bartek"].win_streak) == (3, 0)
    assert (rows["Cezary"].wins, rows["Cezary"].flops, rows["Cezary"].flop_streak) == (0, 5, 5)
    ordered = [r.manager for r in sheet(world).season_facts.rows]
    assert ordered == ["Anna", "Bartek", "Cezary"]


def test_captain_blank_streak_threshold(world):
    world.player(3, "Charlie")
    for gameweek, points in {3: 3, 4: 2, 5: 1}.items():
        world.result(1, gameweek, 90, points)
        world.result(3, gameweek, 90, 9)
        world.gw(10, gameweek, 50, captain=1, vice=2)
        world.gw(11, gameweek, 50, captain=3, vice=2)
        world.gw(12, gameweek, 50)
    rows = {r.manager: r for r in sheet(world).season_facts.rows}
    assert rows["Anna"].captain_blank_streak == 2
    assert rows["Bartek"].captain_blank_streak == 0
    assert rows["Cezary"].captain_blank_streak == 0


def test_records(world):
    play(world, 1, {10: 80, 11: 50, 12: 10})
    play(world, 2, {10: 80, 11: 50, 12: 10})
    season = sheet(world, 2).season_facts
    assert [(r.manager, r.gameweek, r.net_points) for r in season.best_gameweek] == [
        ("Anna", 1, 80),
        ("Anna", 2, 80),
    ]
    assert [(r.manager, r.gameweek, r.net_points) for r in season.worst_gameweek] == [
        ("Cezary", 1, 10),
        ("Cezary", 2, 10),
    ]


def test_nicknames_used_everywhere(world):
    world.result(1, 5, 90, 8)
    world.result(2, 5, 90, 3)
    play(world, 4, {10: 60, 11: 50, 12: 40})
    world.gw(10, 5, 90, captain=1, vice=2, chip="bboost", bench_picks=(2,), bench=3)
    world.gw(11, 5, 70, cost=4, captain=1, vice=2)
    world.gw(12, 5, 20, captain=2, vice=1)
    world.transfer(12, 5, 2, 1)
    result = sheet(world, nicknames={10: "Bartas"})
    text = result.model_dump_json()
    assert "Bartas" in text
    for forbidden in ("Anna", "Alfa", "Realni", "Synthetic XI"):
        assert forbidden not in text
    assert "Bartek" in text
    assert check_fact_sheet(result) == []


def test_season_wins_and_flops_by_net_points_when_a_hit_changes_the_order(world):
    world.gw(10, 1, 64, cost=8)
    world.gw(11, 1, 60)
    world.gw(12, 1, 40)
    world.gw(10, 2, 60)
    world.gw(11, 2, 64, cost=8)
    world.gw(12, 2, 30)
    world.gw(10, 3, 70)
    world.gw(11, 3, 30)
    world.gw(12, 3, 34, cost=8)
    rows = {r.manager: r for r in sheet(world, 3).season_facts.rows}
    assert (rows["Anna"].wins, rows["Anna"].win_streak) == (2, 2)
    assert (rows["Bartek"].wins, rows["Bartek"].win_streak) == (1, 0)
    assert (rows["Cezary"].flops, rows["Cezary"].flop_streak) == (3, 3)
    assert (rows["Bartek"].flops, rows["Anna"].flops) == (0, 0)
