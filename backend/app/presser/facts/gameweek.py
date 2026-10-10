import math
from dataclasses import dataclass

from app.presser.facts.load import (
    AutoSubRow,
    CaptainRef,
    MemberRow,
    PlayerResult,
    SquadPick,
    TransferRow,
)
from app.presser.facts.schema import (
    AutoSub,
    BenchItem,
    BenchTransfersChips,
    Captaincy,
    CaptainPick,
    ChipPlay,
    ChipSquadChange,
    Hit,
    ManagerScore,
    Season,
    SquadPlayer,
    TransferMiss,
)

BLANK_MAX_POINTS = 2
SQUAD_CHIPS = ("freehit", "wildcard")
STARTING_POSITIONS = 11
MAX_SQUAD_CHANGES = 5
GOALKEEPER = 1
# Player.position -> (minimum, maximum) in a starting XI
FORMATION_LIMITS = {1: (1, 1), 2: (3, 5), 3: (2, 5), 4: (1, 3)}


def net_points(row: MemberRow) -> int:
    return row.points - row.transfers_cost


def to_score(row: MemberRow, names: dict[int, str], nth_of_season: int) -> ManagerScore:
    return ManagerScore(
        manager=names[row.entry_id],
        transfers_cost=row.transfers_cost,
        net_points=net_points(row),
        nth_of_season=nth_of_season,
        gameweek_rank=row.gameweek_rank,
    )


def winners_and_flops(
    rows: list[MemberRow],
    names: dict[int, str],
    season: Season,
) -> tuple[list[ManagerScore], list[ManagerScore]]:
    if not rows:
        return [], []
    to_date = {row.manager: row for row in season.rows}
    best = max(net_points(row) for row in rows)
    worst = min(net_points(row) for row in rows)
    winners = [
        to_score(row, names, to_date[names[row.entry_id]].gameweek_wins_to_date)
        for row in rows
        if net_points(row) == best
    ]
    flops = [
        to_score(row, names, to_date[names[row.entry_id]].gameweek_flops_to_date)
        for row in rows
        if net_points(row) == worst
    ]
    return (
        sorted(winners, key=lambda score: score.manager),
        sorted(flops, key=lambda score: score.manager),
    )


def average_net_points(rows: list[MemberRow]) -> int:
    """The league's mean net points, rounded half up to a whole point like FPL's own average."""
    if not rows:
        return 0
    return math.floor(sum(net_points(row) for row in rows) / len(rows) + 0.5)


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


def _minutes(results: dict[tuple[int, int], PlayerResult], player_id: int, gameweek: int) -> int:
    result = results.get((player_id, gameweek))
    return result.minutes if result is not None else 0


def gameweek_before_chip(
    entry_id: int, gameweek: int, chips_by_gameweek: dict[tuple[int, int], str | None]
) -> int | None:
    """The gameweek whose squad the manager had before a Free Hit or Wildcard in `gameweek`.

    A Free Hit squad reverts after its gameweek, so an earlier Free Hit week is skipped. Neither
    chip can be played in a manager's first gameweek, so an earlier gameweek always exists.
    """
    for number in range(gameweek - 1, 0, -1):
        if chips_by_gameweek.get((entry_id, number)) != "freehit":
            return number
    return None


def _can_replace(eleven: list[int | None], out_position: int | None, in_position: int | None):
    if out_position == in_position:
        return True
    if GOALKEEPER in (out_position, in_position):
        return False
    low = FORMATION_LIMITS.get(out_position, (0, 0))[0] if out_position is not None else 0
    high = FORMATION_LIMITS.get(in_position, (0, 11))[1] if in_position is not None else 11
    return eleven.count(out_position) - 1 >= low and eleven.count(in_position) + 1 <= high


def counted_players(
    squad: list[SquadPick],
    results: dict[tuple[int, int], PlayerResult],
    positions: dict[int, int],
    gameweek: int,
) -> set[int]:
    """The players whose points `squad` would score in `gameweek`: the starting XI with FPL's
    automatic substitutions (bench order, a goalkeeper only for the goalkeeper, at least 3
    defenders, 2 midfielders and 1 forward, at most 5, 5 and 3).
    """
    eleven = [pick for pick in squad if pick.position <= STARTING_POSITIONS]
    bench = [pick for pick in squad if pick.position > STARTING_POSITIONS]
    used: set[int] = set()
    for index, starter in enumerate(list(eleven)):
        if _minutes(results, starter.player, gameweek) > 0:
            continue
        for sub in bench:
            if sub.player in used or _minutes(results, sub.player, gameweek) == 0:
                continue
            current = [positions.get(pick.player) for pick in eleven]
            if not _can_replace(current, positions.get(starter.player), positions.get(sub.player)):
                continue
            used.add(sub.player)
            eleven[index] = sub
            break
    return {pick.player for pick in eleven if _minutes(results, pick.player, gameweek) > 0}


def squad_points(
    squad: list[SquadPick],
    results: dict[tuple[int, int], PlayerResult],
    positions: dict[int, int],
    gameweek: int,
) -> int:
    """What `squad` would have scored in `gameweek`, the captain (or the vice) doubled."""
    counted = counted_players(squad, results, positions, gameweek)
    total = sum(_points(results, player, gameweek) for player in counted)
    captain = next((pick.player for pick in squad if pick.is_captain), None)
    vice = next((pick.player for pick in squad if pick.is_vice), None)
    credit = credit_captain(CaptainRef(captain, vice), results, gameweek)
    if credit is not None and credit.player_id in counted:
        total += credit.base_points
    return total


def _chip_effect(
    row: MemberRow,
    bench_ids: list[int],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    positions: dict[int, int],
    gameweek: int,
    previous_squad: list[SquadPick] | None,
) -> int | None:
    if row.active_chip == "bboost":
        return sum(_points(results, player, gameweek) for player in bench_ids)
    if row.active_chip == "3xc":
        ref = refs.get((row.entry_id, gameweek))
        credit = credit_captain(ref, results, gameweek) if ref is not None else None
        return credit.base_points if credit is not None else None
    if row.active_chip in SQUAD_CHIPS:
        if not previous_squad:
            return None
        return net_points(row) - squad_points(previous_squad, results, positions, gameweek)
    return None


def _squad_players(
    picks: list[SquadPick],
    other: set[int],
    counted: set[int],
    results: dict[tuple[int, int], PlayerResult],
    player_names: dict[int, str],
    gameweek: int,
) -> list[SquadPlayer]:
    players = [
        SquadPlayer(
            player=player_names.get(pick.player, "?"),
            points=_points(results, pick.player, gameweek),
            points_counted=pick.player in counted,
        )
        for pick in picks
        if pick.player not in other
    ]
    players.sort(key=lambda item: (-item.points, item.player))
    return players[:MAX_SQUAD_CHANGES]


def squad_change(
    manager: str,
    chip: str,
    previous_squad: list[SquadPick],
    previous_counted: set[int],
    squad: list[SquadPick],
    counted: set[int],
    results: dict[tuple[int, int], PlayerResult],
    player_names: dict[int, str],
    gameweek: int,
) -> ChipSquadChange:
    before = {pick.player for pick in previous_squad}
    after = {pick.player for pick in squad}
    return ChipSquadChange(
        manager=manager,
        chip=chip,
        replaced=len(before - after),
        players_out=_squad_players(
            previous_squad, after, previous_counted, results, player_names, gameweek
        ),
        players_in=_squad_players(squad, before, counted, results, player_names, gameweek),
    )


def _actual_counted(squad: list[SquadPick], subs: list[AutoSubRow]) -> set[int]:
    starters = {pick.player for pick in squad if pick.position <= STARTING_POSITIONS}
    return (starters - {sub.player_out for sub in subs}) | {sub.player_in for sub in subs}


def bench_transfers_chips(
    rows: list[MemberRow],
    bench_picks: dict[int, list[int]],
    transfers: list[TransferRow],
    auto_subs: list[AutoSubRow],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    names: dict[int, str],
    player_names: dict[int, str],
    gameweek: int,
    previous_squads: dict[int, list[SquadPick] | None],
    squads: dict[tuple[int, int], list[SquadPick]],
    positions: dict[int, int],
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
        # A Free Hit or Wildcard rebuilds the squad: one-for-one pairs say nothing there.
        if transfer.entry_id not in by_entry or by_entry[transfer.entry_id].active_chip in (
            SQUAD_CHIPS
        ):
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
                    row,
                    bench_picks.get(row.entry_id, []),
                    refs,
                    results,
                    positions,
                    gameweek,
                    previous_squads.get(row.entry_id),
                ),
            )
            for row in rows
            if row.active_chip
        ),
        key=lambda chip: (chip.effect is None, -(chip.effect or 0), chip.manager),
    )
    subs_by_entry: dict[int, list[AutoSubRow]] = {}
    for sub in auto_subs:
        subs_by_entry.setdefault(sub.entry_id, []).append(sub)
    changes = []
    for row in rows:
        previous_squad = previous_squads.get(row.entry_id)
        squad = squads.get((row.entry_id, gameweek))
        if row.active_chip in SQUAD_CHIPS and previous_squad and squad:
            changes.append(
                squad_change(
                    names[row.entry_id],
                    row.active_chip,
                    previous_squad,
                    counted_players(previous_squad, results, positions, gameweek),
                    squad,
                    _actual_counted(squad, subs_by_entry.get(row.entry_id, [])),
                    results,
                    player_names,
                    gameweek,
                )
            )
    changes.sort(key=lambda change: change.manager)
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
        bench=bench,
        hits=hits,
        transfer_misses=misses,
        chips=chips,
        chip_squad_changes=changes,
        auto_subs=subs,
    )
