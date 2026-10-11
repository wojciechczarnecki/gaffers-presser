from decimal import Decimal

import pytest

from app.presser.facts import load
from tests.presser.helpers import LEAGUE_ID, SEASON, World, live, stats


@pytest.fixture
def world(db):
    world = World(db, gameweeks=6, finished=5)
    world.club(2, "Klub Drugi")
    world.player(1, "Alpha", position=2, club=1)
    world.player(2, "Bravo", position=4, club=2)
    world.manager(10, "Anna Alfa", team_name="Drużyna Anny")
    world.manager(11, "Bartek Beta", team_name="Drużyna Bartka")
    return world


def test_explain_parsed_per_fixture_with_a_double_gameweek(world):
    world.fixture(501, 5, 1, 2, 2, 1)
    world.fixture(502, 5, 2, 1, 0, 0)
    world.result(
        1,
        5,
        150,
        9,
        starts=2,
        explain=[
            stats(501, minutes=(90, 2), goals_scored=(1, 6)),
            stats(502, minutes=(60, 2), clean_sheets=(1, 4)),
        ],
    )
    with world.session() as session:
        results = load.load_match_results(session, SEASON, 5, {1, 2})
    result = results[1]
    assert (result.starts, result.minutes, result.points) == (2, 150, 9)
    assert [f.fixture_id for f in result.fixtures] == [501, 502]
    assert result.fixtures[0].stats["goals_scored"] == load.StatLine(1, 6)
    assert result.fixtures[1].stats["clean_sheets"] == load.StatLine(1, 4)
    assert 2 not in results


def test_minutes_of_a_fixture_default_to_zero(world):
    world.result(1, 5, 0, 0, explain=[stats(501, yellow_cards=(1, -1))])
    with world.session() as session:
        result = load.load_match_results(session, SEASON, 5, {1})[1]
    assert result.fixtures[0].minutes == 0


def test_fixtures_loaded_by_id_with_scores(world):
    world.fixture(501, 5, 1, 2, 2, 1)
    world.fixture(502, 5, 2, 1, None, None)
    world.fixture(503, 5, 1, 2, 0, 0)
    with world.session() as session:
        fixtures = load.load_fixtures(session, SEASON, {501, 502})
    assert fixtures == {
        501: load.FixtureScore(1, 2, 2, 1),
        502: load.FixtureScore(2, 1, None, None),
    }


def test_clubs_map_to_names(world):
    with world.session() as session:
        assert load.load_club_names(session, SEASON) == {1: "Synthetic FC", 2: "Klub Drugi"}


def test_player_info_has_position_and_club(world):
    with world.session() as session:
        info = load.load_player_info(session, SEASON, {1, 2})
    assert info[1] == load.PlayerInfo("Alpha", 2, 1)
    assert info[2] == load.PlayerInfo("Bravo", 4, 2)


def test_ownership_none_without_snapshot_rows(world):
    world.snapshot(6, 1, "9.9")
    world.snapshot(6, 2, "10.0")
    with world.session() as session:
        assert load.load_ownership(session, SEASON, 5) is None
        assert load.load_ownership(session, SEASON, 6) == {1: Decimal("9.9"), 2: Decimal("10.0")}


def test_team_names_per_member(world):
    with world.session() as session:
        names = load.load_team_names(session, SEASON, LEAGUE_ID)
    assert names == {10: "Drużyna Anny", 11: "Drużyna Bartka"}


def test_live_line_parsed_from_live_stats(world):
    world.result(
        1,
        5,
        90,
        2,
        explain=[stats(501, minutes=(90, 2))],
        live=live(goals=2, xg="0.74", xa="0.10", xgc="1.30", tackles=4),
    )
    world.result(2, 5, 90, 2, explain=[stats(501, minutes=(90, 2))])
    with world.session() as session:
        results = load.load_match_results(session, SEASON, 5, {1, 2})
    assert results[1].live == load.LiveLine(
        expected_goals=Decimal("0.74"),
        expected_assists=Decimal("0.10"),
        expected_goals_conceded=Decimal("1.30"),
    )
    assert results[2].live is None


def test_points_modification_counts_in_the_stat_points(world):
    item = stats(501, minutes=(90, 2), bonus=(1, 1))
    item["stats"][1]["points_modification"] = 2
    world.result(1, 5, 90, 5, explain=[item], live=live(xg="0.40"))
    with world.session() as session:
        result = load.load_match_results(session, SEASON, 5, {1})[1]
    assert result.fixtures[0].stats["bonus"] == load.StatLine(1, 3)
    assert result.live is not None
    assert sum(line.points for line in result.fixtures[0].stats.values()) == result.points


def test_explain_not_summing_to_the_points_drops_the_stats(world):
    world.result(1, 5, 90, 7, explain=[stats(501, minutes=(90, 2))], live=live(xg="0.40"))
    with world.session() as session:
        result = load.load_match_results(session, SEASON, 5, {1})[1]
    assert result.points == 7
    assert result.fixtures[0].stats["minutes"] == load.StatLine(90, 2)
    assert result.live is None


@pytest.mark.parametrize(
    "missing", ["expected_goals", "expected_assists", "expected_goals_conceded"]
)
def test_live_stats_missing_a_field_degrade_to_no_stats(world, missing):
    stored = live(goals=1, xg="0.40", xa="0.10", xgc="1.00")
    del stored[missing]
    world.result(1, 5, 90, 2, explain=[stats(501, minutes=(90, 2))], live=stored)
    with world.session() as session:
        result = load.load_match_results(session, SEASON, 5, {1})[1]
    assert result.live is None


def test_live_stats_without_goals_scored_still_load(world):
    stored = live(xg="0.40", xa="0.10", xgc="1.00")
    del stored["goals_scored"]
    world.result(1, 5, 90, 2, explain=[stats(501, minutes=(90, 2))], live=stored)
    with world.session() as session:
        result = load.load_match_results(session, SEASON, 5, {1})[1]
    assert result.live == load.LiveLine(Decimal("0.40"), Decimal("0.10"), Decimal("1.00"))


def test_fixture_bonus_loaded_per_fixture(world):
    for fixture_id in (501, 502, 503):
        world.fixture(fixture_id, 5, 1, 2, 1, 0)
    world.fixture_stat(501, 1, "bps", 30)
    world.fixture_stat(501, 2, "bps", 25, "a")
    world.fixture_stat(501, 1, "bonus", 3)
    world.fixture_stat(501, 2, "yellow_cards", 1, "a")
    world.fixture_stat(502, 2, "bps", 11, "a")
    with world.session() as session:
        bonus = load.load_fixture_bonus(session, SEASON, {501, 502, 503})
    assert bonus == {
        501: load.FixtureBonus(bps={1: 30, 2: 25}, bonus={1: 3}),
        502: load.FixtureBonus(bps={2: 11}, bonus={}),
    }


def test_club_fixture_counts_count_home_and_away(world):
    world.club(3, "Klub Trzeci")
    world.fixture(501, 5, 1, 2, 2, 1)
    world.fixture(502, 5, 2, 1, 0, 0)
    world.fixture(503, 5, 3, 1, 1, 1)
    world.fixture(504, 4, 3, 2, 1, 1)
    with world.session() as session:
        counts = load.load_club_fixture_counts(session, SEASON, 5)
    assert counts == {1: 3, 2: 2, 3: 1}
