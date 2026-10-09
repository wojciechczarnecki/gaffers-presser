from dataclasses import dataclass

from app.presser.facts.load import (
    AutoSubRow,
    CaptainRef,
    MemberRow,
    PlayerResult,
    TransferRow,
)
from app.presser.facts.schema import (
    AutoSub,
    BenchItem,
    BenchTransfersChips,
    Captaincy,
    CaptainPick,
    ChipPlay,
    Hit,
    ManagerScore,
    TransferMiss,
)

BLANK_MAX_POINTS = 2


def net_points(row: MemberRow) -> int:
    return row.points - row.transfers_cost


def to_score(row: MemberRow, names: dict[int, str]) -> ManagerScore:
    return ManagerScore(
        manager=names[row.entry_id],
        points=row.points,
        transfers_cost=row.transfers_cost,
        net_points=net_points(row),
    )


def winners_and_flops(
    rows: list[MemberRow], names: dict[int, str]
) -> tuple[list[ManagerScore], list[ManagerScore]]:
    if not rows:
        return [], []
    best = max(net_points(row) for row in rows)
    worst = min(net_points(row) for row in rows)
    winners = [to_score(row, names) for row in rows if net_points(row) == best]
    flops = [to_score(row, names) for row in rows if net_points(row) == worst]
    return (
        sorted(winners, key=lambda score: score.manager),
        sorted(flops, key=lambda score: score.manager),
    )


def average_points(rows: list[MemberRow]) -> float:
    if not rows:
        return 0.0
    return round(sum(row.points for row in rows) / len(rows), 1)


@dataclass(frozen=True)
class Credit:
    player_id: int
    base_points: int
    vice_stepped_in: bool

    @property
    def blank(self) -> bool:
        return self.base_points <= BLANK_MAX_POINTS


def credit_captain(
    ref: CaptainRef, results: dict[tuple[int, int], PlayerResult], gameweek: int
) -> Credit | None:
    if ref.captain is None:
        return None

    def played(player_id: int | None) -> PlayerResult | None:
        if player_id is None:
            return None
        result = results.get((player_id, gameweek))
        return result if result is not None and result.minutes > 0 else None

    if (result := played(ref.captain)) is not None:
        return Credit(ref.captain, result.points, False)
    if (result := played(ref.vice)) is not None:
        return Credit(ref.vice, result.points, True)
    return Credit(ref.captain, 0, False)


def multiplier_for(chip: str | None) -> int:
    return 3 if chip == "3xc" else 2


def captaincy(
    rows: list[MemberRow],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    names: dict[int, str],
    player_names: dict[int, str],
    gameweek: int,
) -> Captaincy:
    picks = []
    for row in rows:
        ref = refs.get((row.entry_id, gameweek))
        credit = credit_captain(ref, results, gameweek) if ref is not None else None
        if credit is None:
            continue
        multiplier = multiplier_for(row.active_chip)
        picks.append(
            CaptainPick(
                manager=names[row.entry_id],
                player=player_names.get(credit.player_id, "?"),
                base_points=credit.base_points,
                multiplier=multiplier,
                points=credit.base_points * multiplier,
                triple_captain=row.active_chip == "3xc",
                vice_stepped_in=credit.vice_stepped_in,
                blank=credit.blank,
            )
        )
    picks.sort(key=lambda pick: (-pick.points, pick.manager))
    if not picks:
        return Captaincy()
    top = max(pick.points for pick in picks)
    bottom = min(pick.points for pick in picks)
    return Captaincy(
        picks=picks,
        best=sorted(pick.manager for pick in picks if pick.points == top),
        worst=sorted(pick.manager for pick in picks if pick.points == bottom),
    )


def _points(results: dict[tuple[int, int], PlayerResult], player_id: int, gameweek: int) -> int:
    result = results.get((player_id, gameweek))
    return result.points if result is not None else 0


def _chip_effect(
    row: MemberRow,
    average: float,
    bench_ids: list[int],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    gameweek: int,
) -> float | None:
    if row.active_chip == "bboost":
        return float(sum(_points(results, player, gameweek) for player in bench_ids))
    if row.active_chip == "3xc":
        ref = refs.get((row.entry_id, gameweek))
        credit = credit_captain(ref, results, gameweek) if ref is not None else None
        return float(credit.base_points) if credit is not None else None
    if row.active_chip in ("freehit", "wildcard"):
        return round(row.points - average, 1)
    return None


def bench_transfers_chips(
    rows: list[MemberRow],
    bench_picks: dict[int, list[int]],
    transfers: list[TransferRow],
    auto_subs: list[AutoSubRow],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    names: dict[int, str],
    player_names: dict[int, str],
    average: float,
    gameweek: int,
) -> BenchTransfersChips:
    by_entry = {row.entry_id: row for row in rows}

    def player(player_id: int) -> str:
        return player_names.get(player_id, "?")

    bench = sorted(
        (
            BenchItem(manager=names[row.entry_id], points_on_bench=row.points_on_bench)
            for row in rows
            if row.points_on_bench > 0 and row.active_chip != "bboost"
        ),
        key=lambda item: (-item.points_on_bench, item.manager),
    )
    hits = sorted(
        (
            Hit(manager=names[row.entry_id], transfers=row.transfers, cost=row.transfers_cost)
            for row in rows
            if row.transfers_cost > 0
        ),
        key=lambda hit: (-hit.cost, hit.manager),
    )
    misses = []
    for transfer in transfers:
        if transfer.entry_id not in by_entry:
            continue
        points_in = _points(results, transfer.player_in, gameweek)
        points_out = _points(results, transfer.player_out, gameweek)
        if points_in < points_out:
            misses.append(
                TransferMiss(
                    manager=names[transfer.entry_id],
                    player_in=player(transfer.player_in),
                    player_in_points=points_in,
                    player_out=player(transfer.player_out),
                    player_out_points=points_out,
                    difference=points_out - points_in,
                )
            )
    misses.sort(key=lambda miss: (-miss.difference, miss.manager, miss.player_in))
    chips = sorted(
        (
            ChipPlay(
                manager=names[row.entry_id],
                chip=row.active_chip,
                effect=_chip_effect(
                    row, average, bench_picks.get(row.entry_id, []), refs, results, gameweek
                ),
            )
            for row in rows
            if row.active_chip
        ),
        key=lambda chip: (chip.effect is None, -(chip.effect or 0.0), chip.manager),
    )
    subs = sorted(
        (
            AutoSub(
                manager=names[sub.entry_id],
                player_out=player(sub.player_out),
                player_in=player(sub.player_in),
                player_in_points=_points(results, sub.player_in, gameweek),
            )
            for sub in auto_subs
            if sub.entry_id in by_entry
        ),
        key=lambda sub: (-sub.player_in_points, sub.manager, sub.player_out),
    )
    return BenchTransfersChips(
        bench=bench, hits=hits, transfer_misses=misses, chips=chips, auto_subs=subs
    )
