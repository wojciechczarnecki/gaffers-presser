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
    assert (
        rows["Anna"].gameweek_wins_to_date,
        rows["Anna"].gameweek_flops_to_date,
        rows["Anna"].win_streak,
    ) == (3, 0, 1)
    assert (rows["Bartek"].gameweek_wins_to_date, rows["Bartek"].win_streak) == (3, 0)
    assert (
        rows["Cezary"].gameweek_wins_to_date,
        rows["Cezary"].gameweek_flops_to_date,
        rows["Cezary"].flop_streak,
    ) == (0, 5, 5)
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


def test_records_by_gameweek_rank_with_ties(world):
    world.gw(10, 1, 60, gameweek_rank=50_000)
    world.gw(11, 1, 90, gameweek_rank=900_000)
    world.gw(12, 1, 20, gameweek_rank=3_000_000)
    world.gw(10, 2, 70, gameweek_rank=400_000)
    world.gw(11, 2, 40, gameweek_rank=50_000)
    world.gw(12, 2, 55, gameweek_rank=3_000_000)
    season = sheet(world, 2).season_facts
    assert [
        (r.manager, r.gameweek, r.gameweek_rank, r.net_points) for r in season.best_gameweek
    ] == [
        ("Anna", 1, 50_000, 60),
        ("Bartek", 2, 50_000, 40),
    ]
    assert [
        (r.manager, r.gameweek, r.gameweek_rank, r.net_points) for r in season.worst_gameweek
    ] == [
        ("Cezary", 1, 3_000_000, 20),
        ("Cezary", 2, 3_000_000, 55),
    ]


def test_records_skip_rows_without_gameweek_rank(world):
    play(world, 1, {10: 80, 11: 50, 12: 10})
    assert sheet(world, 1).season_facts.best_gameweek == []
    assert sheet(world, 1).season_facts.worst_gameweek == []
    world.gw(10, 2, 80, gameweek_rank=700_000)
    world.gw(11, 2, 50)
    world.gw(12, 2, 10)
    season = sheet(world, 2).season_facts
    assert [(r.manager, r.gameweek) for r in season.best_gameweek] == [("Anna", 2)]
    assert [(r.manager, r.gameweek) for r in season.worst_gameweek] == [("Anna", 2)]


def test_records_ignore_managers_absent_this_gameweek(world):
    world.gw(10, 1, 60, gameweek_rank=400_000)
    world.gw(11, 1, 40, gameweek_rank=900_000)
    world.gw(12, 1, 90, gameweek_rank=5_000)
    world.gw(10, 2, 70, gameweek_rank=200_000)
    world.gw(11, 2, 20, gameweek_rank=3_000_000)
    season = sheet(world, 2).season_facts
    assert [(r.manager, r.gameweek) for r in season.best_gameweek] == [("Anna", 2)]
    assert [(r.manager, r.gameweek) for r in season.worst_gameweek] == [("Bartek", 2)]


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
    for forbidden in ("Anna", "Alfa"):
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
    assert (rows["Anna"].gameweek_wins_to_date, rows["Anna"].win_streak) == (2, 2)
    assert (rows["Bartek"].gameweek_wins_to_date, rows["Bartek"].win_streak) == (1, 0)
    assert (rows["Cezary"].gameweek_flops_to_date, rows["Cezary"].flop_streak) == (3, 3)
    assert (rows["Bartek"].gameweek_flops_to_date, rows["Anna"].gameweek_flops_to_date) == (0, 0)


def test_rows_carry_team_name(world):
    play(world, 5, {10: 30, 11: 70, 12: 20})
    rows = {r.manager: r.team_name for r in sheet(world).table.rows}
    assert rows == {"Anna": "Realni FC 10", "Bartek": "Realni FC 11", "Cezary": "Realni FC 12"}


def test_team_names_argument_overrides_the_stored_ones(world):
    play(world, 5, {10: 30, 11: 70, 12: 20})
    with world.session() as session:
        result = build_fact_sheet(
            session, SEASON, LEAGUE_ID, 5, {}, {10: "Pseudo A", 11: "Pseudo B", 12: "Pseudo C"}
        )
    assert {r.manager: r.team_name for r in result.table.rows}["Bartek"] == "Pseudo B"


def test_winners_and_flops_carry_their_team_name(world):
    play(world, 5, {10: 30, 11: 70, 12: 20})
    with world.session() as session:
        result = build_fact_sheet(
            session, SEASON, LEAGUE_ID, 5, {}, {10: "Pseudo A", 11: "Pseudo B", 12: "Pseudo C"}
        )
    assert [(w.manager, w.team_name) for w in result.winners] == [("Bartek", "Pseudo B")]
    assert [(f.manager, f.team_name) for f in result.flops] == [("Cezary", "Pseudo C")]


@pytest.fixture
def rivals(db):
    world = World(db, gameweeks=6, finished=6)
    for fpl_id, name in [(1, "Alpha"), (2, "Bravo")]:
        world.player(fpl_id, name)
    for entry_id, name in [
        (10, "Anna Alfa"),
        (11, "Bartek Beta"),
        (12, "Cezary Ceta"),
        (13, "Dawid Delta"),
        (14, "Ewa Epsilon"),
    ]:
        world.manager(entry_id, name)
    return world


def neighbours(world, gameweeks, extra=None):
    """Anna just ahead of Bartek, Cezary far ahead, Dawid far behind, for the given gameweeks."""
    for number in gameweeks:
        play(world, number, {10: 60, 11: 58, 12: 90, 13: 10, 14: 1})
    if extra:
        extra()


def swaps_of(world, gameweek):
    return sheet(world, gameweek).table.rivalry_swaps


def test_rivalry_swap_after_three_neighbour_gameweeks(rivals):
    neighbours(rivals, [1, 2, 3])
    play(rivals, 4, {10: 40, 11: 60, 12: 90, 13: 10, 14: 1})
    (swap,) = swaps_of(rivals, 4)
    assert (swap.ahead, swap.behind) == ("Bartek", "Anna")
    assert (swap.ahead_previous_rank, swap.ahead_rank) == (3, 2)
    assert (swap.behind_previous_rank, swap.behind_rank) == (2, 3)
    assert (swap.gap, swap.neighbour_gameweeks) == (14, 3)


def test_rivalry_streak_counts_a_longer_run(rivals):
    neighbours(rivals, [1, 2, 3, 4])
    play(rivals, 5, {10: 40, 11: 60, 12: 90, 13: 10, 14: 1})
    (swap,) = swaps_of(rivals, 5)
    assert swap.neighbour_gameweeks == 4


def test_two_neighbour_gameweeks_no_swap(rivals):
    play(rivals, 1, {10: 60, 11: 58, 12: 90, 13: 10, 14: 59})
    for number in (2, 3):
        play(rivals, number, {10: 60, 11: 58, 12: 90, 13: 10, 14: 0})
    play(rivals, 4, {10: 40, 11: 60, 12: 90, 13: 10, 14: 0})
    assert swaps_of(rivals, 4) == []


def test_no_swap_before_gw4(rivals):
    neighbours(rivals, [1, 2])
    play(rivals, 3, {10: 40, 11: 70, 12: 90, 13: 10, 14: 1})
    assert swaps_of(rivals, 3) == []


def test_tie_now_is_not_a_swap(rivals):
    neighbours(rivals, [1, 2, 3])
    play(rivals, 4, {10: 40, 11: 46, 12: 90, 13: 10, 14: 1})
    assert swaps_of(rivals, 4) == []


def podium(world, gameweek):
    return [
        (c.kind, c.manager, c.previous_rank, c.rank, c.other, c.gap)
        for c in sheet(world, gameweek).table.podium_changes
    ]


def test_podium_new_leader_entered_left_swap(rivals):
    play(rivals, 1, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    play(rivals, 2, {10: 0, 11: 0, 12: 0, 13: 50, 14: 0})
    assert podium(rivals, 2) == [
        ("new_leader", "Dawid", 4, 1, "Bartek", 20),
        ("entered", "Dawid", 4, 1, "Cezary", 40),
        ("left", "Cezary", 3, 4, None, None),
    ]


def test_two_entered_paired_with_the_two_who_left(rivals):
    play(rivals, 1, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    play(rivals, 2, {10: 0, 11: 0, 12: 0, 13: 50, 14: 100})
    assert podium(rivals, 2) == [
        ("new_leader", "Dawid", 4, 1, "Bartek", 20),
        ("entered", "Dawid", 4, 1, "Anna", 30),
        ("entered", "Ewa", 5, 2, "Cezary", 30),
        ("left", "Anna", 2, 4, None, None),
        ("left", "Cezary", 3, 5, None, None),
    ]


def test_podium_swap_carries_the_manager_now_behind(rivals):
    play(rivals, 1, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    play(rivals, 2, {10: 20, 11: 0, 12: 25, 13: 0, 14: 0})
    assert podium(rivals, 2) == [
        ("new_leader", "Anna", 2, 1, "Bartek", 10),
        ("swap", "Cezary", 3, 2, "Bartek", 5),
    ]


def test_podium_ties_ranked_as_in_table(rivals):
    play(rivals, 1, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    play(rivals, 2, {10: 10, 11: 0, 12: 0, 13: 0, 14: 0})
    assert podium(rivals, 2) == [("new_leader", "Anna", 2, 1, "Bartek", 0)]
    rows = sheet(rivals, 2).table.rows
    assert [(r.manager, r.rank) for r in rows[:2]] == [("Anna", 1), ("Bartek", 1)]


def test_no_podium_changes_at_gw1(rivals):
    play(rivals, 1, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    assert podium(rivals, 1) == []


def test_no_podium_change_when_nothing_moves(rivals):
    play(rivals, 1, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    play(rivals, 2, {10: 90, 11: 100, 12: 80, 13: 70, 14: 10})
    assert podium(rivals, 2) == []
