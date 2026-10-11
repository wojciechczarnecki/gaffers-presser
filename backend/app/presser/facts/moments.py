from dataclasses import dataclass

from app.presser.facts.gameweek import net_points
from app.presser.facts.load import MatchResult, MemberRow, PlayerInfo
from app.presser.facts.matches import CLEAN_SHEET_POINTS, FixtureFact
from app.presser.facts.schema import KeyMoment, PlayerStats, Table
from app.presser.facts.stats import moment_breakdown

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


def _pair_moments(
    pair: Pair,
    names: dict[int, str],
    contributions: dict[int, dict[int, int]],
    match_results: dict[int, MatchResult],
    players: dict[int, PlayerInfo],
    club_names: dict[int, str],
    facts: list[FixtureFact],
    player_facts: dict[int, PlayerStats],
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
    for (club_id, _), group in sorted(lost.items()):
        ahead_points = -CLEAN_SHEET_POINTS * sum(ahead.get(p, 0) for p in group)
        behind_points = -CLEAN_SHEET_POINTS * sum(behind.get(p, 0) for p in group)
        differing = sorted(players[p].name for p in group if ahead.get(p, 0) != behind.get(p, 0))
        found.append(
            moment(
                "clean_sheet_lost", differing, club_names.get(club_id), ahead_points, behind_points
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
) -> list[KeyMoment]:
    player_facts = player_facts or {}
    moments: list[KeyMoment] = []
    for pair in _pairs(rows, names, table):
        moments += _pair_moments(
            pair, names, contributions, match_results, players, club_names, facts, player_facts
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
