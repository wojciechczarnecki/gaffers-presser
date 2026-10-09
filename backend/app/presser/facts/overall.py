from fractions import Fraction

from app.presser.facts import ranks
from app.presser.facts.load import MemberRow
from app.presser.facts.schema import Overall, OverallRow


def _row(now: int, before: int | None, name: str, gameweek: int) -> OverallRow:
    first = gameweek == 1
    return OverallRow(
        manager=name,
        overall_rank=now,
        previous_overall_rank=before,
        movement=None if before is None else before - now,
        entered=ranks.thresholds_entered(now, before, first),
        left=ranks.thresholds_left(now, before),
        notable=ranks.is_notable(now, before, first),
    )


def _leaders(rows: list[OverallRow], rising: bool) -> list[str]:
    moved = [
        row
        for row in rows
        if row.notable
        and row.movement is not None
        and (row.movement > 0) == rising
        and row.movement != 0
    ]
    if not moved:
        return []
    best = max(ranks.move_ratio(r.overall_rank, r.previous_overall_rank) for r in moved)
    return sorted(
        r.manager
        for r in moved
        if ranks.move_ratio(r.overall_rank, r.previous_overall_rank) == best
    )


def build_overall(
    current: list[MemberRow], previous: list[MemberRow], names: dict[int, str], gameweek: int
) -> Overall:
    before = {row.entry_id: row.overall_rank for row in previous}
    rows = [
        _row(row.overall_rank, before.get(row.entry_id), names[row.entry_id], gameweek)
        for row in current
        if row.overall_rank is not None
    ]

    def order(row: OverallRow):
        ratio = (
            ranks.move_ratio(row.overall_rank, row.previous_overall_rank)
            if row.previous_overall_rank is not None
            else None
        )
        return (
            not row.notable,
            ratio is None,
            -(ratio if ratio is not None else Fraction(0)),
            row.overall_rank,
            row.manager,
        )

    rows.sort(key=order)
    return Overall(
        rows=rows,
        biggest_climbers=_leaders(rows, rising=True),
        biggest_fallers=_leaders(rows, rising=False),
    )
