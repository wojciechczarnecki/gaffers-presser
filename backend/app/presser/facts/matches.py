from dataclasses import dataclass

from app.presser.facts.gameweek import actual_counted, credit_captain, multiplier_for
from app.presser.facts.load import (
    AutoSubRow,
    CaptainRef,
    FixtureScore,
    FixtureStats,
    MatchResult,
    PlayerInfo,
    PlayerResult,
    SquadPick,
)

CLEAN_SHEET_MIN_MINUTES = 60
CLEAN_SHEET_POINTS = 4
GOALKEEPER = 1
DEFENDER = 2
EVENT_STATS = (
    ("red_cards", "red_card"),
    ("own_goals", "own_goal"),
    ("penalties_missed", "penalty_missed"),
    ("penalties_saved", "penalty_saved"),
)


@dataclass(frozen=True)
class FixtureFact:
    player_id: int
    fixture_id: int
    kind: str
    count: int
    # the stat's points for an event; the clean-sheet points kept, lost or missed otherwise
    points: int


def _conceded(info: PlayerInfo, score: FixtureScore | None) -> int | None:
    if score is None or score.home_score is None or score.away_score is None:
        return None
    if info.club_id == score.home_id:
        return score.away_score
    if info.club_id == score.away_id:
        return score.home_score
    return None


def _started(result: MatchResult, fixture: FixtureStats) -> bool:
    played = sum(1 for item in result.fixtures if item.minutes > 0)
    return played >= 1 and fixture.minutes > 0 and result.starts >= played


def _clean_sheet_fact(
    player_id: int, result: MatchResult, fixture: FixtureStats, conceded: int
) -> FixtureFact | None:
    clean_sheet = fixture.stats.get("clean_sheets")
    kept = clean_sheet is not None and clean_sheet.value > 0
    if kept and conceded >= 1:
        return FixtureFact(player_id, fixture.fixture_id, "lucky_sub", 1, clean_sheet.points)
    if kept:
        return None
    if fixture.minutes >= CLEAN_SHEET_MIN_MINUTES and conceded >= 1:
        return FixtureFact(player_id, fixture.fixture_id, "lost_clean_sheet", 1, CLEAN_SHEET_POINTS)
    if 0 < fixture.minutes < CLEAN_SHEET_MIN_MINUTES and conceded == 0:
        if _started(result, fixture):
            return FixtureFact(player_id, fixture.fixture_id, "unlucky_sub", 1, CLEAN_SHEET_POINTS)
    return None


def fixture_facts(
    results: dict[int, MatchResult],
    players: dict[int, PlayerInfo],
    fixtures: dict[int, FixtureScore],
) -> list[FixtureFact]:
    found: list[FixtureFact] = []
    for player_id in sorted(results):
        result = results[player_id]
        info = players.get(player_id)
        for fixture in result.fixtures:
            for identifier, kind in EVENT_STATS:
                line = fixture.stats.get(identifier)
                if line is not None and line.value > 0:
                    found.append(
                        FixtureFact(player_id, fixture.fixture_id, kind, line.value, line.points)
                    )
            if info is None or info.position not in (GOALKEEPER, DEFENDER):
                continue
            conceded = _conceded(info, fixtures.get(fixture.fixture_id))
            if conceded is None:
                continue
            fact = _clean_sheet_fact(player_id, result, fixture, conceded)
            if fact is not None:
                found.append(fact)
    return found


def contributions(
    squad: list[SquadPick],
    subs: list[AutoSubRow],
    ref: CaptainRef,
    chip: str | None,
    results: dict[tuple[int, int], PlayerResult],
    gameweek: int,
) -> dict[int, int]:
    """Player -> the multiplier his points counted with for the manager."""
    counted = {pick.player for pick in squad} if chip == "bboost" else actual_counted(squad, subs)
    credit = credit_captain(ref, results, gameweek)
    multipliers = dict.fromkeys(counted, 1)
    if credit is not None and credit.player_id in counted:
        multipliers[credit.player_id] = multiplier_for(chip)
    return multipliers
