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


def test_transfer_misses_ordered_by_difference(world):
    for gameweek_points in [(1, 2), (2, 9), (3, 1), (4, 12)]:
        world.result(gameweek_points[0], 5, 90, gameweek_points[1])
    world.gw(10, 5, 60, cost=4)
    world.gw(11, 5, 50, cost=4)
    world.gw(12, 5, 40)
    world.transfer(10, 5, 1, 2)
    world.transfer(11, 5, 3, 4)
    world.transfer(12, 5, 4, 3)
    misses = sheet(world).bench_transfers_chips.transfer_misses
    assert [
        (
            m.manager,
            m.player_in,
            m.player_in_points,
            m.player_out,
            m.player_out_points,
            m.difference,
        )
        for m in misses
    ] == [("Bartek", "Charlie", 1, "Delta", 12, 11), ("Anna", "Alpha", 2, "Bravo", 9, 7)]


def test_hits_with_cost(world):
    world.gw(10, 5, 60, cost=8)
    world.gw(11, 5, 50, cost=4)
    world.gw(12, 5, 40)
    hits = sheet(world).bench_transfers_chips.hits
    assert [(h.manager, h.transfers, h.cost) for h in hits] == [("Anna", 2, 8), ("Bartek", 1, 4)]


def test_chip_effects(world):
    world.result(1, 5, 90, 10)
    world.result(2, 5, 90, 2)
    world.result(3, 5, 0, 0)
    world.result(4, 5, 90, 5)
    world.manager(13, "Dariusz Delta")
    world.manager(14, "Eryk Epsilon")
    world.gw(10, 5, 70, chip="bboost", captain=1, vice=2, bench_picks=(2, 3, 4, 4), bench=7)
    world.gw(11, 5, 60, chip="3xc", captain=1, vice=2)
    world.gw(12, 5, 40, chip="freehit")
    world.gw(13, 5, 30, chip="wildcard")
    world.gw(14, 5, 20, chip="manager")
    result = sheet(world)
    assert result.average_points == 44.0
    chips = {c.manager: (c.chip, c.effect) for c in result.bench_transfers_chips.chips}
    assert chips == {
        "Anna": ("bboost", 12.0),
        "Bartek": ("3xc", 10.0),
        "Cezary": ("freehit", -4.0),
        "Dariusz": ("wildcard", -14.0),
        "Eryk": ("manager", None),
    }
    assert [c.manager for c in result.bench_transfers_chips.chips] == [
        "Anna",
        "Bartek",
        "Cezary",
        "Dariusz",
        "Eryk",
    ]


def test_bench_excludes_bench_boost(world):
    world.gw(10, 5, 60, bench=9, chip="bboost")
    world.gw(11, 5, 50, bench=3)
    world.gw(12, 5, 40, bench=6)
    bench = sheet(world).bench_transfers_chips.bench
    assert [(b.manager, b.points_on_bench) for b in bench] == [("Cezary", 6), ("Bartek", 3)]


def test_auto_subs_listed(world):
    world.result(3, 5, 30, 6)
    world.result(4, 5, 80, 9)
    world.gw(10, 5, 60)
    world.gw(11, 5, 50)
    world.gw(12, 5, 40)
    world.auto_sub(10, 5, 1, 3)
    world.auto_sub(11, 5, 2, 4)
    subs = sheet(world).bench_transfers_chips.auto_subs
    assert [(s.manager, s.player_out, s.player_in, s.player_in_points) for s in subs] == [
        ("Bartek", "Bravo", "Delta", 9),
        ("Anna", "Alpha", "Charlie", 6),
    ]


def test_empty_section_marked(world):
    world.gw(10, 5, 60)
    world.gw(11, 5, 50)
    result = sheet(world)
    assert "bench_transfers_chips" in result.empty_sections
    assert "captaincy" in result.empty_sections
    world.gw(12, 5, 40, bench=4)
    assert "bench_transfers_chips" not in sheet(world).empty_sections


def test_flops_by_net_points_when_a_hit_changes_the_order(world):
    world.gw(10, 5, 60)
    world.gw(11, 5, 44, cost=8)
    world.gw(12, 5, 40)
    result = sheet(world)
    assert [(f.manager, f.points, f.transfers_cost, f.net_points) for f in result.flops] == [
        ("Bartek", 44, 8, 36)
    ]
