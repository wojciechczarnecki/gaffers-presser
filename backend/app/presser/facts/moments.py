from dataclasses import dataclass
from decimal import Decimal

from app.presser.facts.gameweek import net_points
from app.presser.facts.load import MatchResult, MemberRow, PlayerInfo
from app.presser.facts.matches import CLEAN_SHEET_POINTS, DEFENDER, GOALKEEPER, FixtureFact
from app.presser.facts.schema import KeyMoment, PlayerStats, Table
from app.presser.facts.stats import ROUTINE_STATS, NearMiss, moment_breakdown

MAX_KEY_MOMENTS = 5
EVENT_KINDS = ("red_card", "own_goal", "penalty_missed", "penalty_saved")
CLEAN_SHEET_KINDS = ("clean_sheet_lost", "clean_sheet_kept")
PAIR_ORDER = {"winner": 0, "podium": 1, "rivalry": 2}
# a clean sheet lost below or kept from this many expected goals conceded went against the xG
LOST_AGAINST_XG_BELOW = Decimal("0.50")
KEPT_AGAINST_XG_FROM = Decimal("1.50")
# a club's clean sheet worth at least two defenders' points between the pair outranks goals
AMPLIFIED_CLEAN_SHEET_SWING = 2 * CLEAN_SHEET_POINTS
# the cause of a moment, the most interesting first (the owner's order, DECISIONS 2026-10-12):
# events, an amplified clean sheet, goals, assists, a clean sheet against the xG, a plain clean
# sheet, a whole player with no single deciding stat, DefCon, saves, a yellow card, bonus
EVENT_TIER = 0
AMPLIFIED_CLEAN_SHEET_TIER = 1
CLEAN_SHEET_AGAINST_XG_TIER = 4
CLEAN_SHEET_TIER = 5
WHOLE_PLAYER_TIER = 6
BONUS_TIER = 10
STAT_TIERS = {
    "goals_scored": 2,
    "assists": 3,
    "defensive_contribution": 7,
    "saves": 8,
    "yellow_cards": 9,
    "bonus": BONUS_TIER,
}


@dataclass(frozen=True)
class Pair:
    source: str
    ahead: int
    behind: int
    margin: int


def _pairs(rows: list[MemberRow], names: dict[int, str], table: Table) -> list[Pair]:
    by_name = {names[row.entry_id]: row.entry_id for row in rows}
    pairs: list[Pair] = []
    nets = {row.entry_id: net_points(row) for row in rows}
    best = max(nets.values(), default=0)
    rest = [value for value in nets.values() if value < best]
    runner_up = max(rest, default=None)
    if runner_up is not None:
        for winner in sorted(e for e, value in nets.items() if value == best):
            for second in sorted(e for e, value in nets.items() if value == runner_up):
                pairs.append(Pair("winner", winner, second, best - runner_up))
    for change in table.podium_changes:
        # a gap of 0 is a tie broken by the name: no event decided it
        if change.other is not None and change.gap is not None and change.gap > 0:
            pairs.append(Pair("podium", by_name[change.manager], by_name[change.other], change.gap))
    for swap in table.rivalry_swaps:
        pairs.append(Pair("rivalry", by_name[swap.ahead], by_name[swap.behind], swap.gap))
    return pairs


def _decisive(moment: KeyMoment):
    return next((entry for entry in moment.breakdown or [] if entry.decisive), None)


def _decisive_swing(moment: KeyMoment) -> int | None:
    entry = _decisive(moment)
    return None if entry is None else entry.swing


def _tier(moment: KeyMoment) -> int:
    if moment.kind in EVENT_KINDS:
        return EVENT_TIER
    if moment.kind in CLEAN_SHEET_KINDS:
        if moment.swing >= AMPLIFIED_CLEAN_SHEET_SWING:
            return AMPLIFIED_CLEAN_SHEET_TIER
        return CLEAN_SHEET_AGAINST_XG_TIER if moment.against_xg else CLEAN_SHEET_TIER
    if moment.kind == "bonus_missed_by_one":
        return BONUS_TIER
    entry = _decisive(moment)
    return WHOLE_PLAYER_TIER if entry is None else STAT_TIERS[entry.identifier]


def _cause_key(moment: KeyMoment) -> tuple[int, bool, int]:
    """Sorts a pair's moments by the cause: its tier, against the xG first, then its swing."""
    decisive = _decisive_swing(moment)
    swing = moment.swing if decisive is None else decisive
    return (_tier(moment), not moment.against_xg, -swing)


def against_xg(kind: str, conceded_xg: Decimal | None) -> bool | None:
    if conceded_xg is None:
        return None
    if kind == "clean_sheet_lost":
        return conceded_xg < LOST_AGAINST_XG_BELOW
    return conceded_xg >= KEPT_AGAINST_XG_FROM


def _conceded_xg(
    group: list[int],
    fixture_id: int,
    match_results: dict[int, MatchResult],
    players: dict[int, PlayerInfo],
) -> Decimal | None:
    def minutes(player_id: int) -> int:
        fixtures = match_results[player_id].fixtures
        return next((f.minutes for f in fixtures if f.fixture_id == fixture_id), 0)

    candidates = [p for p in group if p in match_results and match_results[p].live is not None]
    if not candidates:
        return None
    best = min(candidates, key=lambda p: (-minutes(p), players[p].name))
    return match_results[best].live.expected_goals_conceded


def _kept_clean_sheets(
    held: set[int], match_results: dict[int, MatchResult], players: dict[int, PlayerInfo]
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """(club, fixture) -> the held goalkeepers and defenders who kept its clean sheet."""
    kept: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for player_id in sorted(held):
        info = players.get(player_id)
        result = match_results.get(player_id)
        if info is None or result is None or info.position not in (GOALKEEPER, DEFENDER):
            continue
        for fixture in result.fixtures:
            line = fixture.stats.get("clean_sheets")
            if line is not None and line.value > 0 and line.points > 0:
                kept.setdefault((info.club_id, fixture.fixture_id), []).append(
                    (player_id, line.points)
                )
    return kept


def _pair_moments(
    pair: Pair,
    names: dict[int, str],
    contributions: dict[int, dict[int, int]],
    match_results: dict[int, MatchResult],
    players: dict[int, PlayerInfo],
    club_names: dict[int, str],
    facts: list[FixtureFact],
    player_facts: dict[int, PlayerStats],
    near_misses: list[NearMiss],
    club_fixture_counts: dict[int, int],
) -> list[KeyMoment]:
    ahead, behind = contributions.get(pair.ahead, {}), contributions.get(pair.behind, {})
    held = set(ahead) | set(behind)

    def club(player_id: int) -> str | None:
        info = players.get(player_id)
        return club_names.get(info.club_id) if info is not None else None

    def moment(kind, players_, club_name, ahead_points, behind_points, **extra) -> KeyMoment:
        return KeyMoment(
            pair=pair.source,
            ahead=names[pair.ahead],
            behind=names[pair.behind],
            margin=pair.margin,
            kind=kind,
            players=players_,
            club=club_name,
            ahead_points=ahead_points,
            behind_points=behind_points,
            swing=ahead_points - behind_points,
            **extra,
        )

    def clean_sheet_moment(kind, club_id, fixture_id, group, ahead_points, behind_points):
        differing = sorted(players[p].name for p in group if ahead.get(p, 0) != behind.get(p, 0))
        conceded_xg = (
            None
            if club_fixture_counts.get(club_id, 1) > 1
            else _conceded_xg(group, fixture_id, match_results, players)
        )
        return moment(
            kind,
            differing,
            club_names.get(club_id),
            ahead_points,
            behind_points,
            expected_goals_conceded=conceded_xg,
            against_xg=against_xg(kind, conceded_xg),
        )

    found: list[KeyMoment] = []
    # a defender in a listed clean sheet moment of his club is told there, not again on his own
    in_club_moment: set[int] = set()
    for (club_id, fixture_id), points in sorted(
        _kept_clean_sheets(held, match_results, players).items()
    ):
        group = [p for p, _ in points]
        kept = clean_sheet_moment(
            "clean_sheet_kept",
            club_id,
            fixture_id,
            group,
            sum(value * ahead.get(p, 0) for p, value in points),
            sum(value * behind.get(p, 0) for p, value in points),
        )
        found.append(kept)
        if kept.swing >= pair.margin:
            in_club_moment |= {p for p in group if ahead.get(p, 0) != behind.get(p, 0)}

    for player_id in sorted(held):
        info = players.get(player_id)
        result = match_results.get(player_id)
        if info is None or result is None:
            continue
        ahead_multiplier, behind_multiplier = ahead.get(player_id, 0), behind.get(player_id, 0)
        player_moment = moment(
            "player",
            [info.name],
            club(player_id),
            result.points * ahead_multiplier,
            result.points * behind_multiplier,
            ahead_multiplier=ahead_multiplier,
            behind_multiplier=behind_multiplier,
            breakdown=moment_breakdown(
                player_facts.get(player_id, PlayerStats()).breakdown,
                ahead_multiplier,
                behind_multiplier,
                pair.margin,
            ),
        )
        if player_id in in_club_moment and _decisive(player_moment) is None:
            # without the clean sheet and the routine points, did the rest of his game decide?
            told = sum(
                entry.swing
                for entry in player_moment.breakdown or []
                if entry.identifier == "clean_sheets" or entry.identifier in ROUTINE_STATS
            )
            if player_moment.swing - told < pair.margin:
                continue
        found.append(player_moment)

    lost: dict[tuple[int, int], list[int]] = {}
    events: dict[tuple[int, str], int] = {}
    for fact in facts:
        if fact.player_id not in held:
            continue
        if fact.kind == "lost_clean_sheet":
            info = players.get(fact.player_id)
            if info is not None:
                lost.setdefault((info.club_id, fact.fixture_id), []).append(fact.player_id)
        elif fact.kind in EVENT_KINDS:
            key = (fact.player_id, fact.kind)
            events[key] = events.get(key, 0) + fact.points

    for (club_id, fixture_id), group in sorted(lost.items()):
        found.append(
            clean_sheet_moment(
                "clean_sheet_lost",
                club_id,
                fixture_id,
                group,
                -CLEAN_SHEET_POINTS * sum(ahead.get(p, 0) for p in group),
                -CLEAN_SHEET_POINTS * sum(behind.get(p, 0) for p in group),
            )
        )
    for (player_id, kind), points in sorted(events.items()):
        info = players.get(player_id)
        if info is None:
            continue
        found.append(
            moment(
                kind,
                [info.name],
                club(player_id),
                points * ahead.get(player_id, 0),
                points * behind.get(player_id, 0),
            )
        )
    for miss in near_misses:
        result = match_results.get(miss.player_id)
        info = players.get(miss.player_id)
        if miss.player_id not in held or info is None or result is None or result.live is None:
            continue
        ahead_multiplier, behind_multiplier = (
            ahead.get(miss.player_id, 0),
            behind.get(miss.player_id, 0),
        )
        ahead_points = miss.missed_bonus * ahead_multiplier
        behind_points = miss.missed_bonus * behind_multiplier
        found.append(
            KeyMoment(
                pair=pair.source,
                ahead=names[pair.ahead],
                behind=names[pair.behind],
                margin=pair.margin,
                kind="bonus_missed_by_one",
                players=[info.name],
                club=club(miss.player_id),
                ahead_points=ahead_points,
                behind_points=behind_points,
                swing=behind_points - ahead_points,
                ahead_multiplier=ahead_multiplier,
                behind_multiplier=behind_multiplier,
                missed_bonus=miss.missed_bonus,
            )
        )
    return [m for m in found if m.swing >= pair.margin or _decisive_swing(m) is not None]


def build_key_moments(
    rows: list[MemberRow],
    names: dict[int, str],
    table: Table,
    contributions: dict[int, dict[int, int]],
    match_results: dict[int, MatchResult],
    players: dict[int, PlayerInfo],
    club_names: dict[int, str],
    facts: list[FixtureFact],
    player_facts: dict[int, PlayerStats] | None = None,
    near_misses: list[NearMiss] | None = None,
    club_fixture_counts: dict[int, int] | None = None,
) -> list[KeyMoment]:
    player_facts = player_facts or {}
    near_misses = near_misses or []
    club_fixture_counts = club_fixture_counts or {}
    moments: list[KeyMoment] = []
    for pair in _pairs(rows, names, table):
        moments += _pair_moments(
            pair,
            names,
            contributions,
            match_results,
            players,
            club_names,
            facts,
            player_facts,
            near_misses,
            club_fixture_counts,
        )
    moments.sort(key=lambda m: (PAIR_ORDER[m.pair], *_cause_key(m), m.ahead, m.behind, m.players))
    # the same event between the same two managers is told once, under its first pair
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    unique: list[KeyMoment] = []
    for moment in moments:
        key = (moment.ahead, moment.behind, moment.kind, tuple(moment.players))
        if key not in seen:
            seen.add(key)
            unique.append(moment)
    return unique[:MAX_KEY_MOMENTS]
