from dataclasses import dataclass
from decimal import Decimal

from app.presser.facts.gameweek import BLANK_MAX_POINTS
from app.presser.facts.load import FixtureBonus, MatchResult
from app.presser.facts.schema import MomentStat, PlayerStats, StatPoints

BIG_MISS_MIN_XG = Decimal("0.70")
GOAL_FROM_NOTHING_BELOW_XG = Decimal("0.15")
HAT_TRICK_MIN_GOALS = 3
KEEPER_WALL_MIN_SAVE_POINTS = 2
OWN_KIND_STATS = ("red_cards", "own_goals", "penalties_missed", "penalties_saved")
# routine points every starter gets or loses: never what decided a moment
ROUTINE_STATS = ("minutes", "goals_conceded")
# the stats that can decide a player moment, the most interesting cause first; the events
# (OWN_KIND_STATS) and a defender's clean sheet are moments of their own
CAUSE_PRIORITY = (
    "goals_scored",
    "assists",
    "defensive_contribution",
    "saves",
    "yellow_cards",
    "bonus",
)


def decisive_identifier(swings: dict[str, int], margin: int) -> str | None:
    """The cause: the first stat in CAUSE_PRIORITY whose own swing reaches the margin."""
    return next((name for name in CAUSE_PRIORITY if swings.get(name, margin - 1) >= margin), None)


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


def moment_breakdown(
    entries: list[StatPoints] | None, ahead_multiplier: int, behind_multiplier: int, margin: int
) -> list[MomentStat] | None:
    if entries is None:
        return None
    swings = {
        entry.identifier: entry.points * (ahead_multiplier - behind_multiplier) for entry in entries
    }
    decisive = decisive_identifier(swings, margin)
    return [
        MomentStat(
            **dict(entry),
            swing=swings[entry.identifier],
            decisive=entry.identifier == decisive,
        )
        for entry in entries
    ]


@dataclass(frozen=True)
class NearMiss:
    player_id: int
    fixture_id: int
    missed_bonus: int


def bonus_near_misses(bonus_by_fixture: dict[int, FixtureBonus]) -> list[NearMiss]:
    found: list[NearMiss] = []
    for fixture_id in sorted(bonus_by_fixture):
        entry = bonus_by_fixture[fixture_id]
        recipients = {player: value for player, value in entry.bonus.items() if value > 0}
        scored = [entry.bps[player] for player in recipients if player in entry.bps]
        if not scored:
            continue
        lowest = min(scored)
        placed = min(
            value for player, value in recipients.items() if entry.bps.get(player) == lowest
        )
        found += [
            NearMiss(player, fixture_id, placed)
            for player, bps in sorted(entry.bps.items())
            if bps == lowest - 1 and player not in recipients
        ]
    return found
