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
