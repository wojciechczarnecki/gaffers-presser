from app.presser.facts.load import MemberRow
from app.presser.facts.schema import PodiumChange, RivalrySwap, Table, TableRow, TopThree


def competition_ranks(totals: dict[int, int]) -> dict[int, int]:
    return {
        entry_id: 1 + sum(other > total for other in totals.values())
        for entry_id, total in totals.items()
    }


def _movements(current: list[MemberRow], previous: list[MemberRow]) -> dict[int, int]:
    before = {row.entry_id: row.total_points for row in previous}
    shared = {row.entry_id: row.total_points for row in current if row.entry_id in before}
    if not shared:
        return {}
    ranks_now = competition_ranks(shared)
    ranks_before = competition_ranks({entry_id: before[entry_id] for entry_id in shared})
    return {entry_id: ranks_before[entry_id] - ranks_now[entry_id] for entry_id in shared}


def build_table(
    current: list[MemberRow],
    previous: list[MemberRow],
    names: dict[int, str],
    team_names: dict[int, str],
) -> Table:
    ordered = sorted(current, key=lambda row: (-row.total_points, names[row.entry_id]))
    ranks = competition_ranks({row.entry_id: row.total_points for row in ordered})
    movements = _movements(current, previous)
    rows = [
        TableRow(
            rank=ranks[row.entry_id],
            manager=names[row.entry_id],
            team_name=team_names[row.entry_id],
            total_points=row.total_points,
            movement=movements.get(row.entry_id),
        )
        for row in ordered
    ]
    leader = rows[0].total_points if rows else 0
    top3 = [
        TopThree(
            manager=row.manager,
            total_points=row.total_points,
            behind_leader=leader - row.total_points,
        )
        for row in rows[:3]
    ]
    gains = [value for value in movements.values() if value > 0]
    losses = [value for value in movements.values() if value < 0]
    best = max(gains) if gains else None
    worst = min(losses) if losses else None
    climbers = sorted(names[e] for e, value in movements.items() if best and value == best)
    fallers = sorted(names[e] for e, value in movements.items() if worst and value == worst)
    return Table(rows=rows, top3=top3, climbers=climbers, fallers=fallers)


RIVALRY_MIN_NEIGHBOUR_GAMEWEEKS = 3
PODIUM_SIZE = 3


def _totals_by_gameweek(all_rows: list[MemberRow]) -> dict[int, dict[int, int]]:
    totals: dict[int, dict[int, int]] = {}
    for row in all_rows:
        totals.setdefault(row.gameweek, {})[row.entry_id] = row.total_points
    return totals


def _order(totals: dict[int, int], names: dict[int, str]) -> list[int]:
    return sorted(totals, key=lambda entry_id: (-totals[entry_id], names[entry_id]))


def _neighbour_streak(
    first: int,
    second: int,
    by_gameweek: dict[int, dict[int, int]],
    names: dict[int, str],
    gameweek: int,
) -> int:
    streak = 0
    for number in range(gameweek - 1, 0, -1):
        totals = by_gameweek.get(number, {})
        if first not in totals or second not in totals:
            break
        order = _order(totals, names)
        if abs(order.index(first) - order.index(second)) != 1:
            break
        streak += 1
    return streak


def rivalry_swaps(
    all_rows: list[MemberRow], names: dict[int, str], gameweek: int
) -> list[RivalrySwap]:
    by_gameweek = _totals_by_gameweek(all_rows)
    now, before = by_gameweek.get(gameweek, {}), by_gameweek.get(gameweek - 1, {})
    ranks_now, ranks_before = competition_ranks(now), competition_ranks(before)
    swaps = []
    for behind in sorted(set(now) & set(before)):
        for ahead in sorted(set(now) & set(before)):
            if not (before[behind] > before[ahead] and now[ahead] > now[behind]):
                continue
            streak = _neighbour_streak(ahead, behind, by_gameweek, names, gameweek)
            if streak < RIVALRY_MIN_NEIGHBOUR_GAMEWEEKS:
                continue
            swaps.append(
                RivalrySwap(
                    ahead=names[ahead],
                    behind=names[behind],
                    ahead_previous_rank=ranks_before[ahead],
                    ahead_rank=ranks_now[ahead],
                    behind_previous_rank=ranks_before[behind],
                    behind_rank=ranks_now[behind],
                    gap=now[ahead] - now[behind],
                    neighbour_gameweeks=streak,
                )
            )
    return sorted(swaps, key=lambda swap: (swap.ahead_rank, swap.ahead, swap.behind))


def podium_changes(
    all_rows: list[MemberRow], names: dict[int, str], gameweek: int
) -> list[PodiumChange]:
    by_gameweek = _totals_by_gameweek(all_rows)
    now, before = by_gameweek.get(gameweek, {}), by_gameweek.get(gameweek - 1, {})
    if gameweek <= 1 or not now or not before:
        return []
    order_now, order_before = _order(now, names), _order(before, names)
    top_now, top_before = order_now[:PODIUM_SIZE], order_before[:PODIUM_SIZE]
    ranks_now, ranks_before = competition_ranks(now), competition_ranks(before)

    def change(kind, entry_id, other=None):
        other = other if other in now else None
        return PodiumChange(
            kind=kind,
            manager=names[entry_id],
            previous_rank=ranks_before.get(entry_id),
            rank=ranks_now[entry_id],
            other=names[other] if other is not None else None,
            gap=now[entry_id] - now[other] if other is not None else None,
        )

    changes: list[PodiumChange] = []
    new_leader_pair: set[int] = set()
    if top_now[0] != top_before[0]:
        changes.append(change("new_leader", top_now[0], top_before[0]))
        new_leader_pair = {top_now[0], top_before[0]}
    left = [entry_id for entry_id in top_before if entry_id not in top_now and entry_id in now]
    for entry_id in top_now:
        if entry_id not in top_before:
            changes.append(change("entered", entry_id, left[0] if left else None))
    changes += [change("left", entry_id) for entry_id in left]
    both = [entry_id for entry_id in top_now if entry_id in top_before]
    for ahead in both:
        for behind in both:
            reversed_order = order_before.index(ahead) > order_before.index(behind)
            if reversed_order and order_now.index(ahead) < order_now.index(behind):
                if {ahead, behind} != new_leader_pair:
                    changes.append(change("swap", ahead, behind))
    return changes
