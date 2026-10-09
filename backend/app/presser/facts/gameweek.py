from dataclasses import dataclass

from app.presser.facts.load import CaptainRef, MemberRow, PlayerResult
from app.presser.facts.schema import Captaincy, CaptainPick, ManagerScore

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
