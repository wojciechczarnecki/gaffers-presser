from dataclasses import dataclass
from decimal import Decimal

from app.presser.facts.gameweek import net_points
from app.presser.facts.load import MatchResult, MemberRow, PlayerInfo
from app.presser.facts.matches import CLEAN_SHEET_POINTS, FixtureFact
from app.presser.facts.schema import KeyMoment, PlayerStats, Table
from app.presser.facts.stats import NearMiss, moment_breakdown

MAX_KEY_MOMENTS = 5
EVENT_KINDS = ("red_card", "own_goal", "penalty_missed", "penalty_saved")
PAIR_ORDER = {"winner": 0, "podium": 1, "rivalry": 2}


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


def _decisive_swing(moment: KeyMoment) -> int | None:
    swings = [entry.swing for entry in moment.breakdown or [] if entry.decisive]
    return max(swings) if swings else None


def _strength(moment: KeyMoment) -> int:
    decisive = _decisive_swing(moment)
    return moment.swing if decisive is None else max(moment.swing, decisive)


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

    found: list[KeyMoment] = []
    for player_id in sorted(held):
        info = players.get(player_id)
        result = match_results.get(player_id)
        if info is None or result is None:
            continue
        ahead_multiplier, behind_multiplier = ahead.get(player_id, 0), behind.get(player_id, 0)
        found.append(
            moment(
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
        )

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
        ahead_points = -CLEAN_SHEET_POINTS * sum(ahead.get(p, 0) for p in group)
        behind_points = -CLEAN_SHEET_POINTS * sum(behind.get(p, 0) for p in group)
        differing = sorted(players[p].name for p in group if ahead.get(p, 0) != behind.get(p, 0))
        found.append(
            moment(
                "clean_sheet_lost",
                differing,
                club_names.get(club_id),
                ahead_points,
                behind_points,
                expected_goals_conceded=(
                    None
                    if club_fixture_counts.get(club_id, 1) > 1
                    else _conceded_xg(group, fixture_id, match_results, players)
                ),
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
    moments.sort(key=lambda m: (PAIR_ORDER[m.pair], -_strength(m), m.ahead, m.behind, m.players))
    # the same event between the same two managers is told once, under its first pair
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    unique: list[KeyMoment] = []
    for moment in moments:
        key = (moment.ahead, moment.behind, moment.kind, tuple(moment.players))
        if key not in seen:
            seen.add(key)
            unique.append(moment)
    return unique[:MAX_KEY_MOMENTS]
