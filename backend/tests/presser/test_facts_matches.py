from app.presser.facts.load import (
    AutoSubRow,
    CaptainRef,
    FixtureScore,
    FixtureStats,
    LiveLine,
    MatchResult,
    PlayerInfo,
    PlayerResult,
    SquadPick,
    StatLine,
)
from app.presser.facts.matches import FixtureFact, contributions, fixture_facts

GOALKEEPER, DEFENDER, MIDFIELDER = 1, 2, 3


def fx(fixture_id: int, **stats: tuple[int, int]) -> FixtureStats:
    return FixtureStats(fixture_id, {name: StatLine(*pair) for name, pair in stats.items()})


def match(*fixtures: FixtureStats, starts: int = 1) -> MatchResult:
    minutes = sum(f.minutes for f in fixtures)
    return MatchResult(starts, minutes, 0, tuple(fixtures))


def info(position: int = DEFENDER, club: int = 1) -> PlayerInfo:
    return PlayerInfo("P", position, club)


HOME_1_0 = {501: FixtureScore(1, 2, 1, 0)}
HOME_0_1 = {501: FixtureScore(1, 2, 0, 1)}


def facts(result, player_info, fixtures):
    return fixture_facts({1: result}, {1: player_info}, fixtures)


def test_red_card_own_goal_penalty_missed_with_points():
    result = match(
        fx(
            501,
            minutes=(40, 1),
            red_cards=(1, -3),
            own_goals=(1, -2),
            penalties_missed=(1, -2),
            yellow_cards=(1, -1),
        )
    )
    assert facts(result, info(MIDFIELDER), HOME_1_0) == [
        FixtureFact(1, 501, "red_card", 1, -3),
        FixtureFact(1, 501, "own_goal", 1, -2),
        FixtureFact(1, 501, "penalty_missed", 1, -2),
    ]


def test_goalkeeper_penalty_save_with_points():
    result = match(fx(501, minutes=(90, 2), clean_sheets=(1, 4), penalties_saved=(1, 5)))
    assert facts(result, info(GOALKEEPER), {501: FixtureScore(1, 2, 0, 0)}) == [
        FixtureFact(1, 501, "penalty_saved", 1, 5),
    ]


def test_double_gameweek_events_per_fixture():
    result = match(
        fx(501, minutes=(90, 2), red_cards=(1, -3)),
        fx(502, minutes=(90, 2), own_goals=(1, -2)),
        starts=2,
    )
    fixtures = {501: FixtureScore(1, 2, 1, 0), 502: FixtureScore(3, 1, 0, 0)}
    assert facts(result, info(MIDFIELDER), fixtures) == [
        FixtureFact(1, 501, "red_card", 1, -3),
        FixtureFact(1, 502, "own_goal", 1, -2),
    ]


def test_lucky_sub():
    result = match(fx(501, minutes=(55, 1), clean_sheets=(1, 4)))
    assert facts(result, info(), HOME_0_1) == [FixtureFact(1, 501, "lucky_sub", 1, 4)]


def test_lucky_sub_needs_the_club_to_concede():
    result = match(fx(501, minutes=(90, 2), clean_sheets=(1, 4)))
    assert facts(result, info(), HOME_1_0) == []


def test_unlucky_sub():
    short = match(fx(501, minutes=(59, 1)))
    assert facts(short, info(), HOME_1_0) == [FixtureFact(1, 501, "unlucky_sub", 1, 4)]
    full = match(fx(501, minutes=(60, 2), clean_sheets=(1, 4)))
    assert facts(full, info(), HOME_1_0) == []


def test_unlucky_sub_needs_a_start():
    bench = match(fx(501, minutes=(20, 1)), starts=0)
    assert facts(bench, info(), HOME_1_0) == []


def test_lost_clean_sheet():
    result = match(fx(501, minutes=(90, 2)))
    assert facts(result, info(), HOME_0_1) == [FixtureFact(1, 501, "lost_clean_sheet", 1, 4)]
    assert facts(match(fx(501, minutes=(59, 1)), starts=1), info(), HOME_0_1) == []
    assert facts(result, info(MIDFIELDER), HOME_0_1) == []


def test_goalkeeper_counts_like_a_defender():
    result = match(fx(501, minutes=(90, 2), saves=(3, 1)))
    assert facts(result, info(GOALKEEPER), HOME_0_1) == [
        FixtureFact(1, 501, "lost_clean_sheet", 1, 4)
    ]


def test_away_club_uses_the_home_score():
    result = match(fx(501, minutes=(90, 2)))
    assert facts(result, info(club=2), HOME_1_0) == [FixtureFact(1, 501, "lost_clean_sheet", 1, 4)]
    assert facts(result, info(club=2), HOME_0_1) == []


def test_unlucky_sub_unknown_in_double_gameweek_with_one_start():
    result = match(
        fx(501, minutes=(30, 1)), fx(502, minutes=(90, 2), clean_sheets=(1, 4)), starts=1
    )
    fixtures = {501: FixtureScore(1, 2, 1, 0), 502: FixtureScore(3, 1, 0, 0)}
    assert facts(result, info(), fixtures) == []
    both = match(fx(501, minutes=(30, 1)), fx(502, minutes=(90, 2), clean_sheets=(1, 4)), starts=2)
    assert facts(both, info(), fixtures) == [FixtureFact(1, 501, "unlucky_sub", 1, 4)]


def test_club_change_skips_clean_sheet_facts():
    result = match(fx(501, minutes=(90, 2), red_cards=(1, -3)))
    assert facts(result, info(club=9), HOME_0_1) == [FixtureFact(1, 501, "red_card", 1, -3)]


def test_missing_score_skips_clean_sheet_facts():
    result = match(fx(501, minutes=(90, 2)))
    assert facts(result, info(), {501: FixtureScore(1, 2, None, None)}) == []


def squad(captain=1, vice=2):
    return [
        SquadPick(position, position, position == captain, position == vice)
        for position in range(1, 16)
    ]


def results(zero_minutes=()):
    return {
        (player, 5): PlayerResult(0 if player in zero_minutes else 90, 3) for player in range(1, 16)
    }


def test_contributions_captain_vice_bench_boost_and_autosub():
    ref = CaptainRef(1, 2)
    base = contributions(squad(), [], ref, None, results(), 5)
    assert base == {**{p: 1 for p in range(3, 12)}, 1: 2, 2: 1}
    triple = contributions(squad(), [], ref, "3xc", results(), 5)
    assert triple[1] == 3
    vice = contributions(squad(), [], ref, None, results({1}), 5)
    assert vice[2] == 2 and vice[1] == 1
    boosted = contributions(squad(), [], ref, "bboost", results(), 5)
    assert set(boosted) == set(range(1, 16))
    subbed = contributions(squad(), [AutoSubRow(7, 7, 13)], ref, None, results({7}), 5)
    assert 7 not in subbed and subbed[13] == 1


def with_live(result: MatchResult) -> MatchResult:
    line = LiveLine(0, 0, 0, 0)
    return MatchResult(result.starts, result.minutes, result.points, result.fixtures, line)


def test_hat_trick_and_keeper_wall_need_live_stats():
    scorer = match(fx(501, minutes=(90, 2), goals_scored=(3, 15)))
    keeper = match(fx(501, minutes=(90, 2), saves=(6, 2)))
    assert facts(scorer, info(MIDFIELDER), HOME_1_0) == []
    assert facts(keeper, info(GOALKEEPER), HOME_1_0) == []
    assert facts(with_live(scorer), info(MIDFIELDER), HOME_1_0) == [
        FixtureFact(1, 501, "hat_trick", 3, 15)
    ]
    assert facts(with_live(keeper), info(GOALKEEPER), HOME_1_0) == [
        FixtureFact(1, 501, "keeper_wall", 6, 2)
    ]
