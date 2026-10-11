from decimal import Decimal

from app.presser.facts.gameweek import BLANK_MAX_POINTS
from app.presser.facts.load import MatchResult
from app.presser.facts.schema import PlayerStats, StatPoints

BIG_MISS_MIN_XG = Decimal("0.70")
GOAL_FROM_NOTHING_BELOW_XG = Decimal("0.15")
HAT_TRICK_MIN_GOALS = 3
KEEPER_WALL_MIN_SAVE_POINTS = 2
OWN_KIND_STATS = ("red_cards", "own_goals", "penalties_missed", "penalties_saved")


def breakdown(result: MatchResult) -> list[StatPoints] | None:
    if result.live is None:
        return None
    totals: dict[str, tuple[int, int]] = {}
    for fixture in result.fixtures:
        for identifier, line in fixture.stats.items():
            value, points = totals.get(identifier, (0, 0))
            totals[identifier] = (value + line.value, points + line.points)
    entries = [
        StatPoints(identifier=identifier, value=value, points=points)
        for identifier, (value, points) in totals.items()
        if points != 0
    ]
    return sorted(entries, key=lambda entry: (-abs(entry.points), entry.identifier))


def player_stat_facts(result: MatchResult) -> PlayerStats:
    entries = breakdown(result)
    if entries is None or result.live is None:
        return PlayerStats()
    goals = next((entry.value for entry in entries if entry.identifier == "goals_scored"), 0)
    return PlayerStats(
        breakdown=entries,
        expected_goals=result.live.expected_goals,
        expected_assists=result.live.expected_assists,
        big_miss=result.points <= BLANK_MAX_POINTS
        and result.live.expected_goals >= BIG_MISS_MIN_XG,
        goal_from_nothing=goals >= 1 and result.live.expected_goals < GOAL_FROM_NOTHING_BELOW_XG,
    )
