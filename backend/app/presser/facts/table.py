from app.presser.facts.load import MemberRow
from app.presser.facts.schema import Table, TableRow, TopThree


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
    current: list[MemberRow], previous: list[MemberRow], names: dict[int, str]
) -> Table:
    ordered = sorted(current, key=lambda row: (-row.total_points, names[row.entry_id]))
    ranks = competition_ranks({row.entry_id: row.total_points for row in ordered})
    movements = _movements(current, previous)
    rows = [
        TableRow(
            rank=ranks[row.entry_id],
            manager=names[row.entry_id],
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
