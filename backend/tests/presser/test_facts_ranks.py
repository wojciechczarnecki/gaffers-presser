import pytest

from tests.presser.helpers import World
from tests.presser.test_facts_table import sheet


@pytest.fixture
def world(db):
    world = World(db)
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name, team_name=f"Realni FC {entry_id}")
    return world


def test_winners_and_flops_carry_gameweek_rank(world):
    world.gw(10, 5, 60, gameweek_rank=50_000)
    world.gw(11, 5, 40, gameweek_rank=800_000)
    world.gw(12, 5, 10, gameweek_rank=4_000_000)
    result = sheet(world)
    assert [(s.manager, s.gameweek_rank) for s in result.winners] == [("Anna", 50_000)]
    assert [(s.manager, s.gameweek_rank) for s in result.flops] == [("Cezary", 4_000_000)]


def test_unknown_gameweek_rank_is_null(world):
    world.gw(10, 5, 60)
    world.gw(11, 5, 40)
    world.gw(12, 5, 10)
    result = sheet(world)
    assert result.winners[0].gameweek_rank is None
    assert result.flops[0].gameweek_rank is None


def test_personal_best_and_worst_need_three_ranked_gameweeks(world):
    for gameweek, rank in {1: 500_000, 2: 400_000, 3: 100_000}.items():
        world.gw(10, gameweek, 50, gameweek_rank=rank)
    world.gw(11, 1, 50, gameweek_rank=300_000)
    world.gw(11, 2, 50, gameweek_rank=100_000)
    world.gw(11, 3, 50, gameweek_rank=900_000)
    world.gw(12, 1, 50, gameweek_rank=300_000)
    world.gw(12, 2, 50)
    world.gw(12, 3, 50, gameweek_rank=100_000)
    season = sheet(world, 3).season_facts
    assert [(p.manager, p.gameweek_rank, p.ranked_gameweeks) for p in season.personal_bests] == [
        ("Anna", 100_000, 3)
    ]
    assert [(p.manager, p.gameweek_rank, p.ranked_gameweeks) for p in season.personal_worsts] == [
        ("Bartek", 900_000, 3)
    ]
    two = sheet(world, 2).season_facts
    assert two.personal_bests == [] and two.personal_worsts == []


def test_equal_rank_is_not_a_personal_best_or_worst(world):
    for gameweek, rank in {1: 400_000, 2: 400_000, 3: 400_000}.items():
        world.gw(10, gameweek, 50, gameweek_rank=rank)
    season = sheet(world, 3).season_facts
    assert season.personal_bests == [] and season.personal_worsts == []


def _overall(world, gameweek, ranks):
    for entry_id, rank in ranks.items():
        world.gw(entry_id, gameweek, 50, overall_rank=rank)


def test_overall_section_rows_and_movers(world):
    world.manager(13, "Dariusz Delta")
    _overall(world, 4, {10: 300_000, 11: 3_000_000, 12: 900_000, 13: 500_000})
    _overall(world, 5, {10: 100_000, 11: 1_400_000, 12: 1_900_000, 13: 490_000})
    overall = sheet(world).overall
    assert [
        (
            r.manager,
            r.overall_rank,
            r.previous_overall_rank,
            r.movement,
            r.entered,
            r.left,
            r.notable,
        )
        for r in overall.rows
    ] == [
        ("Anna", 100_000, 300_000, 200_000, [100_000], [], True),
        ("Bartek", 1_400_000, 3_000_000, 1_600_000, [], [], True),
        ("Cezary", 1_900_000, 900_000, -1_000_000, [], [1_000_000], True),
        ("Dariusz", 490_000, 500_000, 10_000, [], [], False),
    ]
    assert overall.biggest_climbers == ["Anna"]
    assert overall.biggest_fallers == ["Cezary"]
    assert "overall" not in sheet(world).empty_sections


def test_overall_first_gameweek_has_no_movement(world):
    _overall(world, 1, {10: 9_000, 11: 2_000_000, 12: 80_000})
    overall = sheet(world, 1).overall
    assert [(r.manager, r.movement, r.entered, r.notable) for r in overall.rows] == [
        ("Anna", None, [1_000_000, 100_000, 10_000], True),
        ("Cezary", None, [1_000_000, 100_000], True),
        ("Bartek", None, [], False),
    ]
    assert overall.biggest_climbers == [] and overall.biggest_fallers == []


def test_overall_empty_when_nothing_notable(world):
    _overall(world, 4, {10: 2_000_000, 11: 3_000_000, 12: 1_500_000})
    _overall(world, 5, {10: 1_900_000, 11: 3_100_000, 12: 1_400_000})
    result = sheet(world)
    assert "overall" in result.empty_sections
    assert len(result.overall.rows) == 3
    assert result.overall.biggest_climbers == [] and result.overall.biggest_fallers == []


def test_overall_skips_managers_without_overall_rank(world):
    _overall(world, 5, {10: 5_000})
    world.gw(11, 5, 40)
    assert [r.manager for r in sheet(world).overall.rows] == ["Anna"]
