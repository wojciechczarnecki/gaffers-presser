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


def test_chip_effects_are_whole_points(world):
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
    chips = {c.manager: (c.chip, c.effect) for c in result.bench_transfers_chips.chips}
    # no squad before the Free Hit or Wildcard is known, so their effect is unknown
    assert chips == {
        "Anna": ("bboost", 12),
        "Bartek": ("3xc", 10),
        "Cezary": ("freehit", None),
        "Dariusz": ("wildcard", None),
        "Eryk": ("manager", None),
    }
    assert all(isinstance(c.effect, int) for c in result.bench_transfers_chips.chips[:2])
    assert [c.manager for c in result.bench_transfers_chips.chips] == [
        "Anna",
        "Bartek",
        "Cezary",
        "Dariusz",
        "Eryk",
    ]


GK, DEF, MID, FWD = 1, 2, 3, 4
# a 1-3-5-2 starting XI 101-111 and a bench 112 (GK), 113 (FWD), 114 (DEF), 115 (MID)
POSITIONS = {101: GK, 102: DEF, 103: DEF, 104: DEF, 110: FWD, 111: FWD, 112: GK, 113: FWD, 114: DEF}


@pytest.fixture
def squad_world(world):
    for fpl_id in range(101, 121):
        world.player(fpl_id, f"P{fpl_id}", position=POSITIONS.get(fpl_id, MID))
    return world


def test_free_hit_effect_is_points_against_the_squad_before_it(squad_world):
    world = squad_world
    world.gw(10, 4, 50)
    world.squad(10, 4, list(range(101, 112)), [112, 113, 114, 115], captain=102, vice=103)
    special = {102: (0, 0), 103: (90, 6), 113: (90, 5), 114: (90, 4)}
    for player in range(101, 116):
        world.result(player, 5, *special.get(player, (90, 2)))
    world.gw(10, 5, 70, chip="freehit")
    world.squad(10, 5, [101, *range(103, 112), 116], [112, 113, 114, 115], captain=116, vice=103)
    world.result(116, 5, 90, 14)
    world.gw(11, 5, 50)
    # the defender 102 did not play: the forward 113 would break the three-defender minimum, so
    # the defender 114 comes on; the vice 103 is doubled
    # 101 + 103..111 = 2 + 6 + 8 * 2 = 24, 114 = 4, vice +6 -> 34
    (chip,) = sheet(world).bench_transfers_chips.chips
    assert (chip.manager, chip.chip, chip.effect) == ("Anna", "freehit", 70 - 34)


def test_bench_goalkeeper_replaces_only_the_goalkeeper(squad_world):
    world = squad_world
    world.gw(10, 4, 50)
    world.squad(10, 4, list(range(101, 112)), [113, 112], captain=103, vice=104)
    for player in range(102, 112):
        world.result(player, 5, 90, 1)
    world.result(101, 5, 0, 0)
    world.result(112, 5, 90, 3)
    world.result(113, 5, 90, 4)
    world.gw(10, 5, 40, chip="wildcard")
    world.squad(10, 5, list(range(101, 112)), [113, 112], captain=103, vice=104)
    world.gw(11, 5, 50)
    # the forward 113 is first on the bench but cannot replace the goalkeeper; 112 does:
    # 10 starters * 1 + 112 = 3 + captain 103 +1 -> 14 (with the forward it would be 15)
    (chip,) = sheet(world).bench_transfers_chips.chips
    assert chip.effect == 40 - 14


def test_squad_before_a_free_hit_skips_an_earlier_free_hit_week(squad_world):
    world = squad_world
    world.gw(10, 3, 50)
    world.squad(10, 3, [105], captain=105)
    world.gw(10, 4, 60, chip="freehit")
    world.squad(10, 4, [116], captain=116)
    world.result(105, 5, 90, 5)
    world.result(116, 5, 90, 9)
    world.gw(10, 5, 30, chip="wildcard")
    world.squad(10, 5, [117], captain=117)
    world.gw(11, 5, 50)
    (chip,) = sheet(world).bench_transfers_chips.chips
    assert chip.effect == 30 - 10  # the GW3 squad: 105 with the armband


def test_free_hit_week_lists_squad_changes_instead_of_one_for_one_misses(squad_world):
    world = squad_world
    world.gw(10, 4, 50)
    world.squad(10, 4, [105, 106, 107], [115], captain=106, vice=107)
    world.result(106, 5, 90, 14)
    world.result(107, 5, 90, 3)
    world.result(116, 5, 90, 2)
    world.result(117, 5, 0, 0)
    world.gw(10, 5, 40, chip="freehit")
    world.squad(10, 5, [105, 116], [117, 115], captain=116, vice=105)
    world.transfer(10, 5, 117, 106)
    world.transfer(10, 5, 116, 107)
    world.gw(11, 5, 50)
    world.transfer(11, 5, 117, 106)
    extras = sheet(world).bench_transfers_chips
    assert [m.manager for m in extras.transfer_misses] == ["Bartek"]
    (change,) = extras.chip_squad_changes
    assert (change.manager, change.chip, change.replaced) == ("Anna", "freehit", 2)
    assert [(p.player, p.points, p.points_counted) for p in change.players_out] == [
        ("P106", 14, True),
        ("P107", 3, True),
    ]
    assert [(p.player, p.points, p.points_counted) for p in change.players_in] == [
        ("P116", 2, True),
        ("P117", 0, False),
    ]


def test_squad_change_says_whose_points_counted(squad_world):
    world = squad_world
    world.gw(10, 4, 50)
    world.squad(10, 4, [105], [106], captain=105)
    world.result(105, 5, 90, 2)
    world.result(106, 5, 90, 14)  # dropped from the old bench: would not have counted
    world.result(116, 5, 0, 0)
    world.result(117, 5, 90, 9)  # brought in on the bench, auto-subbed on: counted
    world.gw(10, 5, 40, chip="freehit")
    world.squad(10, 5, [105, 116], [117], captain=105)
    world.auto_sub(10, 5, 116, 117)
    world.gw(11, 5, 50)
    (change,) = sheet(world).bench_transfers_chips.chip_squad_changes
    assert [(p.player, p.points_counted) for p in change.players_out] == [("P106", False)]
    assert [(p.player, p.points_counted) for p in change.players_in] == [
        ("P117", True),
        ("P116", False),
    ]


def test_squad_change_lists_at_most_five_each_and_counts_every_swap(squad_world):
    world = squad_world
    world.gw(10, 4, 50)
    world.squad(10, 4, list(range(101, 108)), captain=101)
    world.gw(10, 5, 40, chip="wildcard")
    world.squad(10, 5, [101, *range(114, 120)], captain=101)
    world.gw(11, 5, 50)
    (change,) = sheet(world).bench_transfers_chips.chip_squad_changes
    assert change.replaced == 6
    assert len(change.players_out) == 5
    assert len(change.players_in) == 5


def test_winners_and_flops_say_which_win_or_flop_of_the_season_it_is(world):
    world.gw(10, 4, 70)
    world.gw(11, 4, 30)
    world.gw(12, 4, 50)
    world.gw(10, 5, 80)
    world.gw(11, 5, 20)
    world.gw(12, 5, 50)
    result = sheet(world)
    assert [(w.manager, w.nth_of_season) for w in result.winners] == [("Anna", 2)]
    assert [(f.manager, f.nth_of_season) for f in result.flops] == [("Bartek", 2)]
    rows = {row.manager: row for row in result.season_facts.rows}
    assert rows["Anna"].gameweek_wins_to_date == 2


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
