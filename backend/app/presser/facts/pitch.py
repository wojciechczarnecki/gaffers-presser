from decimal import Decimal

from app.presser.facts.gameweek import BLANK_MAX_POINTS
from app.presser.facts.load import MatchResult, PlayerInfo, SquadPick
from app.presser.facts.matches import FixtureFact
from app.presser.facts.schema import (
    Pitch,
    PitchEvent,
    PitchItem,
    PitchKind,
    PitchOwner,
    PlayerStats,
)

HAUL_MIN_POINTS = 10
GLOBAL_DIFFERENTIAL_BELOW = Decimal("10")
LEAGUE_DIFFERENTIAL_MAX_MANAGERS = 2
MAX_PITCH_ITEMS = 8

EVENT_KINDS = ("red_card", "own_goal", "penalty_missed", "penalty_saved")
SUB_KINDS = ("lucky_sub", "unlucky_sub")
SCORE_KINDS = ("differential_haul", "template_haul", "template_blank")


def _is_template(in_squad: int, managers: int) -> bool:
    return in_squad >= 1 and in_squad * 2 >= managers


def _events(facts: list[FixtureFact]) -> list[PitchEvent]:
    merged: dict[str, tuple[int, int]] = {}
    for fact in facts:
        if fact.kind in EVENT_KINDS + SUB_KINDS:
            count, points = merged.get(fact.kind, (0, 0))
            merged[fact.kind] = (count + fact.count, points + fact.points)
    return [
        PitchEvent(kind=kind, count=count, points=points)
        for kind, (count, points) in sorted(merged.items())
    ]


def build_pitch(
    names: dict[int, str],
    squads: dict[int, list[SquadPick]],
    contributions: dict[int, dict[int, int]],
    match_results: dict[int, MatchResult],
    players: dict[int, PlayerInfo],
    club_names: dict[int, str],
    facts: list[FixtureFact],
    ownership: dict[int, Decimal] | None,
    moment_players: set[str],
    managers: int,
    player_facts: dict[int, PlayerStats],
) -> Pitch:
    in_squad: dict[int, set[int]] = {}
    for entry_id, squad in squads.items():
        for pick in squad:
            in_squad.setdefault(pick.player, set()).add(entry_id)
    owners: dict[int, list[tuple[int, int]]] = {}
    for entry_id, multipliers in contributions.items():
        for player_id, multiplier in multipliers.items():
            owners.setdefault(player_id, []).append((entry_id, multiplier))
    facts_by_player: dict[int, list[FixtureFact]] = {}
    for fact in facts:
        facts_by_player.setdefault(fact.player_id, []).append(fact)

    items: list[PitchItem] = []
    for player_id in sorted(set(owners) | set(in_squad)):
        info = players.get(player_id)
        result = match_results.get(player_id)
        if info is None:
            continue
        points = result.points if result is not None else 0
        holders = sorted(owners.get(player_id, []), key=lambda held: names[held[0]])
        template = _is_template(len(in_squad.get(player_id, ())), managers)
        percent = ownership.get(player_id) if ownership is not None else None
        global_flag = None if percent is None else percent < GLOBAL_DIFFERENTIAL_BELOW
        league_flag = 1 <= len(holders) <= LEAGUE_DIFFERENTIAL_MAX_MANAGERS
        events = _events(facts_by_player.get(player_id, []))

        kinds: list[PitchKind] = []
        if points >= HAUL_MIN_POINTS and (global_flag or league_flag):
            kinds.append("differential_haul")
        if template and points >= HAUL_MIN_POINTS:
            kinds.append("template_haul")
        if template and points <= BLANK_MAX_POINTS:
            kinds.append("template_blank")
        kinds += [event.kind for event in events]
        template_kind = "template_haul" in kinds or "template_blank" in kinds
        if not kinds or not (holders or template_kind):
            continue
        squad_holders = in_squad.get(player_id, set())
        counted = {entry_id for entry_id, _ in holders}
        items.append(
            PitchItem(
                player=info.name,
                club=club_names.get(info.club_id, "?"),
                points=points,
                kinds=kinds,
                owners=[PitchOwner(manager=names[e], multiplier=m) for e, m in holders],
                benched=sorted(names[e] for e in squad_holders - counted),
                without=(
                    sorted(names[e] for e in set(squads) - squad_holders) if template_kind else []
                ),
                global_differential=global_flag,
                league_differential=league_flag,
                events=events,
                **dict(player_facts.get(player_id, PlayerStats())),
            )
        )

    def rank(item: PitchItem):
        return (
            item.player not in moment_players,
            not any(kind in EVENT_KINDS for kind in item.kinds),
            not any(kind in SCORE_KINDS for kind in item.kinds),
            -item.points,
            item.player,
        )

    items.sort(key=rank)
    return Pitch(items=items[:MAX_PITCH_ITEMS])
