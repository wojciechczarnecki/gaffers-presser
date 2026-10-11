import pytest

from app.presser.facts import build_fact_sheet
from tests.presser.helpers import LEAGUE_ID, SEASON, World


def sheet(world: World, gameweek: int = 5):
    with world.session() as session:
        return build_fact_sheet(session, SEASON, LEAGUE_ID, gameweek, {})


@pytest.fixture
def world(db):
    world = World(db)
    for fpl_id, name, points in [
        (1, "Out6", 2),
        (2, "In6", 8),
        (3, "Out5", 2),
        (4, "In5", 7),
        (5, "Out1", 1),
        (6, "In12", 13),
        (7, "Out9", 9),
        (8, "In2", 2),
    ]:
        world.player(fpl_id, name, {5: (90, points)})
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name)
    return world


def test_transfer_win_boundary_6_listed_5_not(world):
    world.gw(10, 5, 60)
    world.gw(11, 5, 50)
    world.transfer(10, 5, 2, 1)
    world.transfer(11, 5, 4, 3)
    wins = sheet(world).bench_transfers.transfer_wins
    assert [(w.manager, w.player_in, w.player_out, w.difference) for w in wins] == [
        ("Anna", "In6", "Out6", 6)
    ]


def test_no_transfer_wins_in_free_hit_or_wildcard_week(world):
    world.gw(10, 5, 60, chip="freehit")
    world.gw(11, 5, 50, chip="wildcard")
    world.gw(12, 5, 40)
    world.transfer(10, 5, 6, 5)
    world.transfer(11, 5, 6, 5)
    world.transfer(12, 5, 6, 5)
    wins = sheet(world).bench_transfers.transfer_wins
    assert [w.manager for w in wins] == ["Cezary"]


def test_transfer_wins_ranked_by_difference(world):
    for entry_id in (10, 11, 12):
        world.gw(entry_id, 5, 50)
    world.transfer(10, 5, 2, 1)
    world.transfer(11, 5, 6, 5)
    world.transfer(12, 5, 6, 7)
    wins = sheet(world).bench_transfers.transfer_wins
    assert [(w.manager, w.difference) for w in wins] == [("Bartek", 12), ("Anna", 6)]
    assert (wins[0].player_in_points, wins[0].player_out_points) == (13, 1)


def test_hit_gain_sums_all_transfers(world):
    world.gw(10, 5, 60, cost=8)
    world.gw(11, 5, 50)
    world.transfer(10, 5, 2, 1)  # +6
    world.transfer(10, 5, 6, 7)  # +4
    world.transfer(10, 5, 8, 7)  # -7
    (hit,) = sheet(world).bench_transfers.hits
    assert (hit.manager, hit.cost, hit.transfer_gain) == ("Anna", 8, 3)
    assert [(p.player_in, p.player_out) for p in hit.pairs] == [
        ("In12", "Out9"),
        ("In2", "Out9"),
        ("In6", "Out6"),
    ]


def test_hit_paid_off_only_above_cost(world):
    world.gw(10, 5, 60, cost=4)
    world.gw(11, 5, 50, cost=4)
    world.gw(12, 5, 50, cost=4)
    world.transfer(10, 5, 4, 3)  # +5, above the cost of 4
    world.transfer(11, 5, 8, 7)  # -7
    world.transfer(12, 5, 2, 3)  # +6
    hits = {h.manager: (h.transfer_gain, h.paid_off) for h in sheet(world).bench_transfers.hits}
    assert hits["Anna"] == (5, True)
    assert hits["Bartek"] == (-7, False)
    assert hits["Cezary"] == (6, True)


def test_gain_equal_to_the_cost_is_not_paid_off(world):
    world.player(20, "Gain4", {5: (90, 6)})
    world.gw(10, 5, 60, cost=4)
    world.gw(11, 5, 50)
    world.transfer(10, 5, 20, 1)  # 6 - 2 = 4
    (hit,) = sheet(world).bench_transfers.hits
    assert (hit.transfer_gain, hit.paid_off) == (4, False)
