from decimal import Decimal

import pytest

from app.presser.facts import build_fact_sheet, check_fact_sheet
from app.presser.facts.load import FixtureStats, LiveLine, MatchResult, StatLine
from app.presser.facts.schema import FactSheet, StatPoints
from app.presser.facts.stats import breakdown
from tests.presser.helpers import LEAGUE_ID, SEASON, World, live, stats


def sheet(world: World, gameweek: int = 5):
    with world.session() as session:
        return build_fact_sheet(session, SEASON, LEAGUE_ID, gameweek, {})


@pytest.fixture
def world(db):
    world = World(db)
    world.club(2, "Klub Drugi")
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta"), (12, "Cezary Ceta")]:
        world.manager(entry_id, name)
    return world


def player(world, player_id, name, points, explain, live_stats=None, position=3, minutes=90):
    world.player(player_id, name, position=position, club=1)
    world.result(player_id, 5, minutes, points, explain=explain, live=live_stats)


def pick(world, entry_id, points, starters, captain=None, vice=None):
    world.gw(entry_id, 5, points)
    world.squad(entry_id, 5, starters, captain=captain, vice=vice)


def match_result(*items, live_line=None):
    return MatchResult(
        starts=1,
        minutes=90,
        points=0,
        fixtures=tuple(
            FixtureStats(fixture_id, {k: StatLine(*v) for k, v in lines.items()})
            for fixture_id, lines in items
        ),
        live=live_line,
    )


LIVE_LINE = LiveLine(0, Decimal("0"), Decimal("0"), Decimal("0"))


def test_breakdown_sums_explain_over_a_double_gameweek(world):
    world.fixture(501, 5, 1, 2, 3, 0)
    world.fixture(502, 5, 2, 1, 0, 1)
    player(
        world,
        1,
        "Double",
        19,
        [
            stats(501, minutes=(90, 2), goals_scored=(2, 10)),
            stats(502, minutes=(90, 2), goals_scored=(1, 5)),
        ],
        live(goals=3, xg="1.20"),
    )
    pick(world, 10, 60, [1])
    pick(world, 11, 50, [])
    pick(world, 12, 40, [])
    (item,) = sheet(world).pitch.items
    assert item.breakdown == [
        StatPoints(identifier="goals_scored", value=3, points=15),
        StatPoints(identifier="minutes", value=180, points=4),
    ]


def test_breakdown_keeps_no_zero_point_entry_and_none_without_live_stats():
    unused = match_result((501, {"minutes": (0, 0)}), live_line=LIVE_LINE)
    assert breakdown(unused) == []
    assert breakdown(match_result((501, {"minutes": (90, 2)}))) is None


def test_breakdown_orders_by_points_magnitude_then_identifier():
    result = match_result(
        (501, {"minutes": (90, 2), "yellow_cards": (1, -1), "bonus": (1, 1), "saves": (4, 1)}),
        live_line=LIVE_LINE,
    )
    assert [entry.identifier for entry in breakdown(result)] == [
        "minutes",
        "bonus",
        "saves",
        "yellow_cards",
    ]


def test_pitch_item_and_captain_carry_the_breakdown(world):
    player(world, 1, "Absent", 0, [stats(501, minutes=(0, 0))], live(), minutes=0)
    player(
        world,
        2,
        "Vice",
        12,
        [stats(501, minutes=(90, 2), goals_scored=(1, 10))],
        live(goals=1, xg="0.50"),
    )
    pick(world, 10, 60, [1, 2], captain=1, vice=2)
    pick(world, 11, 50, [])
    pick(world, 12, 40, [])
    result = sheet(world)
    (captain,) = result.captaincy.picks
    assert (captain.player, captain.vice_stepped_in, captain.base_points) == ("Vice", True, 12)
    assert sum(entry.points for entry in captain.breakdown) == captain.base_points
    (item,) = result.pitch.items
    assert item.player == "Vice"
    assert sum(entry.points for entry in item.breakdown) == item.points


def captains(world, specs):
    """specs: (points, xg, goals) per captain; manager 10.. each captain; all hold player 1."""
    explain = {
        0: [stats(501, minutes=(90, 2))],
        1: [stats(501, minutes=(90, 2), goals_scored=(1, 5))],
    }
    starters = []
    for number, (points, xg, goals) in enumerate(specs, start=1):
        player(
            world,
            number,
            f"P{number}",
            points,
            explain[goals] if points != 3 else [stats(501, minutes=(90, 2), bonus=(1, 1))],
            live(goals=goals, xg=xg),
        )
        starters.append(number)
    for index, entry_id in enumerate((10, 11, 12)[: len(specs)]):
        pick(world, entry_id, 60 - index, [1, index + 1] if index else [1], captain=index + 1)
    return {pick_.manager: pick_ for pick_ in sheet(world).captaincy.picks}


def test_big_miss_boundary_070_and_069(world):
    picks = captains(world, [(2, "0.70", 0), (2, "0.69", 0), (3, "0.90", 0)])
    assert [picks[m].big_miss for m in ("Anna", "Bartek", "Cezary")] == [True, False, False]
    (item,) = [i for i in sheet(world).pitch.items if i.player == "P1"]
    assert item.big_miss is True
    assert item.expected_goals == Decimal("0.70")


def test_goal_from_nothing_boundary_014_and_015(world):
    picks = captains(world, [(7, "0.14", 1), (7, "0.15", 1)])
    assert [picks[m].goal_from_nothing for m in ("Anna", "Bartek")] == [True, False]


def test_xg_flags_false_without_condition_and_null_without_stats(world):
    player(world, 1, "Quiet", 5, [stats(501, minutes=(90, 2), assists=(1, 3))], live(xg="0.50"))
    player(world, 2, "Unknown", 5, [stats(501, minutes=(90, 2), assists=(1, 3))])
    pick(world, 10, 60, [1], captain=1)
    pick(world, 11, 50, [2], captain=2)
    pick(world, 12, 40, [])
    picks = {p.manager: p for p in sheet(world).captaincy.picks}
    quiet, unknown = picks["Anna"], picks["Bartek"]
    assert (quiet.big_miss, quiet.goal_from_nothing) == (False, False)
    assert quiet.expected_goals == Decimal("0.50")
    assert quiet.expected_assists == Decimal("0.00")
    assert (unknown.breakdown, unknown.expected_goals, unknown.expected_assists) == (
        None,
        None,
        None,
    )
    assert (unknown.big_miss, unknown.goal_from_nothing) == (None, None)


DATA_ONLY = {
    "ict_index",
    "influence",
    "creativity",
    "threat",
    "tackles",
    "recoveries",
    "clearances_blocks_interceptions",
    "defensive_contribution",
    "in_dreamteam",
    "expected_goal_involvements",
}


def test_data_only_stats_absent_from_the_schema():
    schema = FactSheet.model_json_schema()
    names = {name for node in schema["$defs"].values() for name in node.get("properties", {})}
    names |= set(schema["properties"])
    assert not DATA_ONLY & names


def test_gameweek_without_match_stats_builds_a_v4_sheet(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    player(world, 1, "Haulman", 14, [])
    player(world, 2, "Sent", -2, [stats(501, minutes=(40, 1), red_cards=(1, -3))])
    player(world, 3, "Scorer", 17, [stats(501, minutes=(90, 2), goals_scored=(3, 15))])
    player(world, 4, "Wall", 4, [stats(501, minutes=(90, 2), saves=(6, 2))], position=1)
    for fixture_player, bps, bonus in [(1, 30, 3), (2, 28, 2), (3, 26, 1)]:
        world.fixture_stat(501, fixture_player, "bps", bps)
        world.fixture_stat(501, fixture_player, "bonus", bonus)
    world.fixture_stat(501, 4, "bps", 25)
    pick(world, 10, 60, [1], captain=1)
    pick(world, 11, 55, [2, 3, 4])
    pick(world, 12, 40, [])
    result = sheet(world)
    assert result.version == 4
    kinds = {i.player: i.kinds for i in result.pitch.items}
    assert kinds == {
        "Haulman": ["differential_haul"],
        "Sent": ["red_card"],
        "Scorer": ["differential_haul"],
    }
    for item in result.pitch.items:
        assert (item.breakdown, item.expected_goals, item.big_miss, item.goal_from_nothing) == (
            None,
            None,
            None,
            None,
        )
    for captain in result.captaincy.picks:
        assert (captain.breakdown, captain.expected_assists, captain.big_miss) == (None, None, None)
    assert [(m.kind, m.players, m.swing) for m in result.key_moments] == [
        ("player", ["Haulman"], 28)
    ]
    assert check_fact_sheet(result) == []


def test_hat_trick_needs_three_goals_in_one_fixture(world):
    world.fixture(501, 5, 1, 2, 3, 0)
    world.fixture(502, 5, 2, 1, 0, 1)
    player(
        world,
        1,
        "Trio",
        17,
        [stats(501, minutes=(90, 2), goals_scored=(3, 15))],
        live(goals=3, xg="1.50"),
    )
    player(
        world,
        2,
        "Split",
        19,
        [
            stats(501, minutes=(90, 2), goals_scored=(2, 10)),
            stats(502, minutes=(90, 2), goals_scored=(1, 5)),
        ],
        live(goals=3, xg="1.50"),
    )
    pick(world, 10, 30, [1, 2])
    pick(world, 11, 20, [])
    pick(world, 12, 10, [])
    kinds = {i.player: i.kinds for i in sheet(world).pitch.items}
    assert kinds["Trio"] == ["differential_haul", "hat_trick"]
    assert kinds["Split"] == ["differential_haul"]


def test_keeper_wall_needs_two_save_points_in_one_fixture(world):
    explain = {
        "wall": [stats(501, minutes=(90, 2), saves=(6, 2))],
        "short": [stats(501, minutes=(90, 2), saves=(5, 1))],
    }
    world.fixture(501, 5, 1, 2, 0, 0)
    player(world, 1, "Wall", 4, explain["wall"], live(), position=1)
    player(world, 2, "Short", 3, explain["short"], live(), position=1)
    player(world, 3, "Defender", 4, explain["wall"], live(), position=2)
    pick(world, 10, 30, [1, 2, 3])
    pick(world, 11, 20, [])
    pick(world, 12, 10, [])
    kinds = {i.player: i.kinds for i in sheet(world).pitch.items}
    assert kinds == {"Wall": ["keeper_wall"]}


def test_hat_trick_of_an_uncounted_player_not_listed(world):
    explain = [stats(501, minutes=(90, 2), goals_scored=(3, 15))]
    player(world, 1, "Benched", 17, explain, live(goals=3))
    player(world, 2, "Nobody", 17, explain, live(goals=3))
    world.gw(10, 5, 30)
    world.squad(10, 5, [], [1])
    world.gw(11, 5, 20)
    world.squad(11, 5, [], [1])
    world.gw(12, 5, 10)
    world.squad(12, 5, [])
    items = {i.player: i for i in sheet(world).pitch.items}
    assert items["Benched"].kinds == ["template_haul"]
    assert "Nobody" not in items


def test_new_kinds_carry_differential_flags(world):
    trio = [stats(501, minutes=(90, 2), goals_scored=(3, 15))]
    wall = [stats(501, minutes=(90, 2), saves=(6, 2))]
    player(world, 1, "Trio", 17, trio, live(goals=3))
    player(world, 2, "Wall", 4, wall, live(), position=1)
    pick(world, 10, 30, [1, 2])
    pick(world, 11, 20, [])
    pick(world, 12, 10, [])
    world.snapshot(5, 1, "3.0")
    world.snapshot(5, 2, "40.0")
    items = {i.player: i for i in sheet(world).pitch.items}
    assert (items["Trio"].global_differential, items["Trio"].league_differential) == (True, True)
    assert "keeper_wall" in items["Wall"].kinds
    assert (items["Wall"].global_differential, items["Wall"].league_differential) == (False, True)


def test_new_kinds_rank_with_hauls(world):
    hat = [stats(501, minutes=(90, 2), goals_scored=(3, 15))]
    player(world, 1, "Haul", 12, [stats(501, minutes=(90, 2), goals_scored=(2, 10))], live(goals=2))
    player(world, 2, "Hat", 17, hat, live(goals=3))
    player(world, 3, "Wall", 4, [stats(501, saves=(8, 2), minutes=(90, 2))], live(), position=1)
    player(world, 4, "Sent", -2, [stats(501, minutes=(40, 1), red_cards=(1, -3))], live())
    pick(world, 10, 90, [1, 2, 3, 4])
    pick(world, 11, 40, [])
    pick(world, 12, 10, [])
    assert [i.player for i in sheet(world).pitch.items] == ["Sent", "Hat", "Haul", "Wall"]


def test_bonus_near_miss_without_live_stats_is_not_a_moment(world):
    world.fixture(501, 5, 1, 2, 0, 0)
    player(world, 1, "Near", 2, [stats(501, minutes=(90, 2))])
    for filler, bps, bonus in [(20, 30, 3), (21, 28, 2), (22, 25, 1)]:
        world.player(filler, f"Filler{filler}")
        world.fixture_stat(501, filler, "bps", bps)
        world.fixture_stat(501, filler, "bonus", bonus)
    world.fixture_stat(501, 1, "bps", 24)
    pick(world, 10, 60, [])
    pick(world, 11, 59, [1])
    pick(world, 12, 10, [])
    assert [m.kind for m in sheet(world).key_moments] == []
