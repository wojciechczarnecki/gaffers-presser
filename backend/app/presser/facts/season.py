from collections import defaultdict

from app.presser.facts.gameweek import credit_captain, net_points
from app.presser.facts.load import CaptainRef, MemberRow, PlayerResult
from app.presser.facts.schema import Record, Season, SeasonRow


def _records(rows: list[MemberRow], names: dict[int, str]) -> tuple[list[Record], list[Record]]:
    if not rows:
        return [], []
    best = max(net_points(row) for row in rows)
    worst = min(net_points(row) for row in rows)

    def pick(value: int) -> list[Record]:
        return sorted(
            (
                Record(manager=names[row.entry_id], gameweek=row.gameweek, net_points=value)
                for row in rows
                if net_points(row) == value
            ),
            key=lambda record: (record.gameweek, record.manager),
        )

    return pick(best), pick(worst)


def _streak(flags: dict[int, bool], gameweek: int) -> int:
    count = 0
    for number in range(gameweek, 0, -1):
        if not flags.get(number, False):
            break
        count += 1
    return count


def _wins_and_flops(
    all_rows: list[MemberRow],
) -> tuple[dict[int, dict[int, bool]], dict[int, dict[int, bool]]]:
    by_gameweek: dict[int, list[MemberRow]] = defaultdict(list)
    for row in all_rows:
        by_gameweek[row.gameweek].append(row)
    won: dict[int, dict[int, bool]] = defaultdict(dict)
    flopped: dict[int, dict[int, bool]] = defaultdict(dict)
    for number, rows in by_gameweek.items():
        best = max(net_points(row) for row in rows)
        worst = min(net_points(row) for row in rows)
        for row in rows:
            won[row.entry_id][number] = net_points(row) == best
            flopped[row.entry_id][number] = net_points(row) == worst
    return won, flopped


def build_season(
    all_rows: list[MemberRow],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    names: dict[int, str],
    gameweek: int,
) -> Season:
    won, flopped = _wins_and_flops(all_rows)
    blank: dict[int, dict[int, bool]] = defaultdict(dict)
    for row in all_rows:
        ref = refs.get((row.entry_id, row.gameweek))
        credit = credit_captain(ref, results, row.gameweek) if ref is not None else None
        blank[row.entry_id][row.gameweek] = credit is not None and credit.blank

    current = [row for row in all_rows if row.gameweek == gameweek]
    rows = sorted(
        (
            SeasonRow(
                manager=names[row.entry_id],
                gameweek_wins_to_date=sum(won[row.entry_id].values()),
                gameweek_flops_to_date=sum(flopped[row.entry_id].values()),
                win_streak=_streak(won[row.entry_id], gameweek),
                flop_streak=_streak(flopped[row.entry_id], gameweek),
                captain_blank_streak=_streak(blank[row.entry_id], gameweek),
            )
            for row in current
        ),
        key=lambda item: (-item.gameweek_wins_to_date, item.gameweek_flops_to_date, item.manager),
    )
    best_records, worst_records = _records(all_rows, names)
    return Season(rows=rows, best_gameweek=best_records, worst_gameweek=worst_records)
