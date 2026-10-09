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
