import pytest

from app.presser.facts import NoFactsError, build_fact_sheet
from tests.presser.helpers import LEAGUE_ID, SEASON, World


def sheet(world: World, gameweek: int = 5, nicknames=None):
    with world.session() as session:
        return build_fact_sheet(session, SEASON, LEAGUE_ID, gameweek, nicknames or {})


@pytest.fixture
def world(db):
    world = World(db)
    for fpl_id, name in [(1, "Alpha"), (2, "Bravo"), (3, "Charlie"), (4, "Delta")]:
        world.player(fpl_id, name)
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name)
    return world


def test_winners_by_net_points_with_tie(world):
    world.gw(10, 5, 60, cost=0)
    world.gw(11, 5, 64, cost=4)
    world.gw(12, 5, 41, cost=0)
    result = sheet(world)
    assert [(w.manager, w.points, w.transfers_cost, w.net_points) for w in result.winners] == [
        ("Anna", 60, 0, 60),
        ("Bartek", 64, 4, 60),
    ]
    assert [(f.manager, f.net_points) for f in result.flops] == [("Cezary", 41)]
    assert result.managers == 3
    assert result.average_points == 55.0


def test_flops_tie_gives_several(world):
    world.gw(10, 5, 60)
    world.gw(11, 5, 30)
    world.gw(12, 5, 30)
    assert [f.manager for f in sheet(world).flops] == ["Bartek", "Cezary"]


def test_manager_without_team_in_no_section(world):
    world.gw(10, 5, 60, captain=1, vice=2)
    world.gw(11, 5, 50, captain=1, vice=2)
    world.gw(12, 5, 0, has_team=False)
    world.result(1, 5, 90, 6)
    result = sheet(world)
    assert result.managers == 2
    text = result.model_dump_json()
    assert "Cezary" not in text


def test_no_members_with_team_raises(world):
    world.gw(10, 5, 0, has_team=False)
    with pytest.raises(NoFactsError):
        sheet(world)


def test_captain_points_with_multiplier(world):
    world.result(1, 5, 90, 8)
    world.result(2, 5, 90, 2)
    world.gw(10, 5, 60, captain=1, vice=2)
    world.gw(11, 5, 50, captain=2, vice=1)
    world.gw(12, 5, 40, captain=2, vice=1)
    captaincy = sheet(world).captaincy
    assert [
        (p.manager, p.player, p.base_points, p.multiplier, p.points) for p in captaincy.picks
    ] == [
        ("Anna", "Alpha", 8, 2, 16),
        ("Bartek", "Bravo", 2, 2, 4),
        ("Cezary", "Bravo", 2, 2, 4),
    ]
    assert captaincy.best == ["Anna"]
    assert captaincy.worst == ["Bartek", "Cezary"]
    assert [p.blank for p in captaincy.picks] == [False, True, True]


def test_triple_captain_marked(world):
    world.result(1, 5, 90, 8)
    world.gw(10, 5, 60, chip="3xc", captain=1, vice=2)
    pick = sheet(world).captaincy.picks[0]
    assert (pick.triple_captain, pick.multiplier, pick.points) == (True, 3, 24)


def test_vice_credited_when_captain_played_zero_minutes(world):
    world.result(1, 5, 0, 0)
    world.result(2, 5, 70, 7)
    world.gw(10, 5, 60, captain=1, vice=2)
    pick = sheet(world).captaincy.picks[0]
    assert (pick.player, pick.vice_stepped_in, pick.base_points, pick.points) == (
        "Bravo",
        True,
        7,
        14,
    )


def test_captain_credited_with_zero_when_vice_also_missed(world):
    world.result(1, 5, 0, 0)
    world.gw(10, 5, 60, captain=1, vice=2)
    pick = sheet(world).captaincy.picks[0]
    assert (pick.player, pick.vice_stepped_in, pick.points, pick.blank) == ("Alpha", False, 0, True)


def test_missing_result_row_counts_as_zero_minutes(world):
    world.result(2, 5, 60, 5)
    world.gw(10, 5, 60, captain=1, vice=2)
    pick = sheet(world).captaincy.picks[0]
    assert (pick.player, pick.vice_stepped_in) == ("Bravo", True)
