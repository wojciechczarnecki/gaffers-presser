from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SECTIONS = (
    "winners",
    "flops",
    "captaincy",
    "pitch",
    "bench_transfers",
    "chips",
    "table",
    "overall",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagerScore(Strict):
    manager: str
    transfers_cost: int
    net_points: int
    # this gameweek's win (in `winners`) or flop (in `flops`) is the Nth of the season
    nth_of_season: int
    gameweek_rank: int | None = None


class StatPoints(Strict):
    identifier: str
    # summed over the gameweek's fixtures
    value: int
    points: int


class MomentStat(StatPoints):
    # points * (ahead multiplier - behind multiplier)
    swing: int
    # at most one per breakdown: the first stat in CAUSE_PRIORITY whose own swing reaches the margin
    decisive: bool


class PlayerStats(Strict):
    # null: the gameweek has no match stats for the player; [] means no point-bearing stat
    breakdown: list[StatPoints] | None = None
    expected_goals: Decimal | None = None
    expected_assists: Decimal | None = None
    big_miss: bool | None = None
    goal_from_nothing: bool | None = None


class CaptainPick(PlayerStats):
    # the breakdown is in captain points: each stat's points * multiplier, summing to points
    manager: str
    player: str
    base_points: int
    multiplier: int
    points: int
    triple_captain: bool
    vice_stepped_in: bool
    blank: bool


class CaptainGroup(Strict):
    # the managers who captained the same player with the same multiplier, counted
    player: str
    multiplier: int
    points: int
    managers: int


class Captaincy(Strict):
    picks: list[CaptainPick] = Field(default_factory=list)
    groups: list[CaptainGroup] = Field(default_factory=list)
    best: list[str] = Field(default_factory=list)
    worst: list[str] = Field(default_factory=list)


class BenchItem(Strict):
    manager: str
    points_on_bench: int


class TransferPair(Strict):
    player_in: str
    player_in_points: int
    player_out: str
    player_out_points: int


class Hit(Strict):
    manager: str
    transfers: int
    cost: int
    # the sum over the manager's transfers of (points in - points out)
    transfer_gain: int
    paid_off: bool
    pairs: list[TransferPair] = Field(default_factory=list)


class TransferMiss(Strict):
    manager: str
    player_in: str
    player_in_points: int
    player_out: str
    player_out_points: int
    difference: int


class TransferWin(TransferMiss):
    pass


class ChipPlay(Strict):
    manager: str
    chip: str
    effect: int | None


class SquadPlayer(Strict):
    player: str
    points: int
    # whether the points counted (players in) or would have counted (players out) for the manager
    points_counted: bool


class ChipSquadChange(Strict):
    manager: str
    chip: str
    replaced: int
    players_out: list[SquadPlayer] = Field(default_factory=list)
    players_in: list[SquadPlayer] = Field(default_factory=list)


class AutoSub(Strict):
    manager: str
    player_out: str
    player_in: str
    player_in_points: int


class BenchTransfers(Strict):
    bench: list[BenchItem] = Field(default_factory=list)
    hits: list[Hit] = Field(default_factory=list)
    transfer_misses: list[TransferMiss] = Field(default_factory=list)
    transfer_wins: list[TransferWin] = Field(default_factory=list)
    auto_subs: list[AutoSub] = Field(default_factory=list)


class ChipCount(Strict):
    chip: str
    managers: int


class Chips(Strict):
    plays: list[ChipPlay] = Field(default_factory=list)
    counts: list[ChipCount] = Field(default_factory=list)
    squad_changes: list[ChipSquadChange] = Field(default_factory=list)


PitchKind = Literal[
    "differential_haul",
    "template_haul",
    "template_blank",
    "hat_trick",
    "keeper_wall",
    "red_card",
    "own_goal",
    "penalty_missed",
    "penalty_saved",
    "lucky_sub",
    "unlucky_sub",
]


class PitchOwner(Strict):
    manager: str
    # 1, 2 (captain) or 3 (Triple Captain)
    multiplier: int


class PitchEvent(Strict):
    kind: Literal[
        "red_card", "own_goal", "penalty_missed", "penalty_saved", "lucky_sub", "unlucky_sub"
    ]
    count: int
    # the stat's points for a card, own goal or missed penalty (negative) or a saved penalty
    # (positive); the clean-sheet points kept (lucky sub) or missed (unlucky sub)
    points: int


class PitchItem(PlayerStats):
    player: str
    club: str
    points: int
    kinds: list[PitchKind]
    owners: list[PitchOwner] = Field(default_factory=list)
    benched: list[str] = Field(default_factory=list)
    without: list[str] = Field(default_factory=list)
    # null: the overall ownership at the deadline is unknown
    global_differential: bool | None = None
    league_differential: bool = False
    events: list[PitchEvent] = Field(default_factory=list)


class Pitch(Strict):
    items: list[PitchItem] = Field(default_factory=list)


class KeyMoment(Strict):
    pair: Literal["winner", "podium", "rivalry"]
    ahead: str
    behind: str
    margin: int
    kind: Literal[
        "player",
        "clean_sheet_lost",
        "clean_sheet_kept",
        "red_card",
        "own_goal",
        "penalty_missed",
        "penalty_saved",
        "bonus_missed_by_one",
    ]
    players: list[str]
    club: str | None = None
    ahead_points: int
    behind_points: int
    # ahead_points - behind_points; for bonus_missed_by_one the points are the bonus each
    # manager would have gained, and the swing is behind_points - ahead_points
    swing: int
    # kinds player and bonus_missed_by_one
    ahead_multiplier: int | None = None
    behind_multiplier: int | None = None
    # kind player only
    breakdown: list[MomentStat] | None = None
    # kind bonus_missed_by_one only
    missed_bonus: int | None = None
    # clean-sheet kinds only; null in a double gameweek or without stats
    expected_goals_conceded: Decimal | None = None
    # clean-sheet kinds only: lost at a low or kept at a high expected_goals_conceded;
    # null when it is unknown
    against_xgc: bool | None = None


class TableRow(Strict):
    rank: int
    manager: str
    team_name: str
    total_points: int
    movement: int | None


class TopThree(Strict):
    manager: str
    total_points: int
    behind_leader: int


class RivalrySwap(Strict):
    # `ahead` overtook `behind` this gameweek
    ahead: str
    behind: str
    ahead_previous_rank: int
    ahead_rank: int
    behind_previous_rank: int
    behind_rank: int
    gap: int
    neighbour_gameweeks: int


class PodiumChange(Strict):
    kind: Literal["new_leader", "entered", "left", "swap"]
    manager: str
    previous_rank: int | None
    rank: int
    other: str | None = None
    gap: int | None = None


class Table(Strict):
    rows: list[TableRow] = Field(default_factory=list)
    top3: list[TopThree] = Field(default_factory=list)
    climbers: list[str] = Field(default_factory=list)
    fallers: list[str] = Field(default_factory=list)
    rivalry_swaps: list[RivalrySwap] = Field(default_factory=list)
    podium_changes: list[PodiumChange] = Field(default_factory=list)


class SeasonRow(Strict):
    manager: str
    gameweek_wins_to_date: int
    gameweek_flops_to_date: int
    win_streak: int
    flop_streak: int
    captain_blank_streak: int


class Record(Strict):
    manager: str
    gameweek: int
    gameweek_rank: int
    net_points: int


class PersonalRank(Strict):
    manager: str
    gameweek_rank: int
    ranked_gameweeks: int


class Season(Strict):
    rows: list[SeasonRow] = Field(default_factory=list)
    best_gameweek: list[Record] = Field(default_factory=list)
    worst_gameweek: list[Record] = Field(default_factory=list)
    personal_bests: list[PersonalRank] = Field(default_factory=list)
    personal_worsts: list[PersonalRank] = Field(default_factory=list)


class OverallRow(Strict):
    manager: str
    overall_rank: int
    previous_overall_rank: int | None
    # previous - now; positive = climbed; None without a previous rank
    movement: int | None
    entered: list[int] = Field(default_factory=list)
    left: list[int] = Field(default_factory=list)
    notable: bool


class Overall(Strict):
    rows: list[OverallRow] = Field(default_factory=list)
    biggest_climbers: list[str] = Field(default_factory=list)
    biggest_fallers: list[str] = Field(default_factory=list)


class FactSheet(Strict):
    version: Literal[6] = 6
    league: str
    season: str
    gameweek: int
    managers: int
    average_net_points: int
    winners: list[ManagerScore]
    flops: list[ManagerScore]
    captaincy: Captaincy = Field(default_factory=Captaincy)
    pitch: Pitch = Field(default_factory=Pitch)
    key_moments: list[KeyMoment] = Field(default_factory=list)
    bench_transfers: BenchTransfers = Field(default_factory=BenchTransfers)
    chips: Chips = Field(default_factory=Chips)
    table: Table = Field(default_factory=Table)
    overall: Overall = Field(default_factory=Overall)
    season_facts: Season = Field(default_factory=Season)
    empty_sections: list[str] = Field(default_factory=list)


def empty_sections(sheet: FactSheet) -> list[str]:
    extras = sheet.bench_transfers
    empty = {
        "winners": not sheet.winners,
        "flops": not sheet.flops,
        "captaincy": not sheet.captaincy.picks,
        "pitch": not (sheet.pitch.items or sheet.key_moments),
        "bench_transfers": not (
            extras.bench
            or extras.hits
            or extras.transfer_misses
            or extras.transfer_wins
            or extras.auto_subs
        ),
        "chips": not (sheet.chips.plays or sheet.chips.squad_changes),
        "table": not sheet.table.rows,
        "overall": not any(row.notable for row in sheet.overall.rows),
    }
    return [section for section in SECTIONS if empty[section]]
