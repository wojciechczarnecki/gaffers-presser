from app.presser.facts.gameweek import (
    SQUAD_CHIPS,
    actual_counted,
    counted_players,
    credit_captain,
    net_points,
    player_points,
    squad_points,
)
from app.presser.facts.load import (
    AutoSubRow,
    CaptainRef,
    MemberRow,
    PlayerResult,
    SquadPick,
)
from app.presser.facts.schema import ChipPlay, Chips, ChipSquadChange, SquadPlayer

MAX_SQUAD_CHANGES = 5


def chip_effect(
    row: MemberRow,
    bench_ids: list[int],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    positions: dict[int, int],
    gameweek: int,
    previous_squad: list[SquadPick] | None,
) -> int | None:
    if row.active_chip == "bboost":
        return sum(player_points(results, player, gameweek) for player in bench_ids)
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
            points=player_points(results, pick.player, gameweek),
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


def build_chips(
    rows: list[MemberRow],
    bench_picks: dict[int, list[int]],
    auto_subs: list[AutoSubRow],
    refs: dict[tuple[int, int], CaptainRef],
    results: dict[tuple[int, int], PlayerResult],
    names: dict[int, str],
    player_names: dict[int, str],
    gameweek: int,
    previous_squads: dict[int, list[SquadPick] | None],
    squads: dict[tuple[int, int], list[SquadPick]],
    positions: dict[int, int],
) -> Chips:
    plays = sorted(
        (
            ChipPlay(
                manager=names[row.entry_id],
                chip=row.active_chip,
                effect=chip_effect(
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
                    actual_counted(squad, subs_by_entry.get(row.entry_id, [])),
                    results,
                    player_names,
                    gameweek,
                )
            )
    changes.sort(key=lambda change: change.manager)
    return Chips(plays=plays, squad_changes=changes)
