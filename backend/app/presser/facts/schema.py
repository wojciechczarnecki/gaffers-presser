from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SECTIONS = ("winners", "flops", "captaincy", "bench_transfers_chips", "table")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagerScore(Strict):
    manager: str
    points: int
    transfers_cost: int
    net_points: int
    # this gameweek's win (in `winners`) or flop (in `flops`) is the Nth of the season
    nth_of_season: int


class CaptainPick(Strict):
    manager: str
    player: str
    base_points: int
    multiplier: int
    points: int
    triple_captain: bool
    vice_stepped_in: bool
    blank: bool


class Captaincy(Strict):
    picks: list[CaptainPick] = Field(default_factory=list)
    best: list[str] = Field(default_factory=list)
    worst: list[str] = Field(default_factory=list)


class BenchItem(Strict):
    manager: str
    points_on_bench: int


class Hit(Strict):
    manager: str
    transfers: int
    cost: int


class TransferMiss(Strict):
    manager: str
    player_in: str
    player_in_points: int
    player_out: str
    player_out_points: int
    difference: int


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


class BenchTransfersChips(Strict):
    bench: list[BenchItem] = Field(default_factory=list)
    hits: list[Hit] = Field(default_factory=list)
    transfer_misses: list[TransferMiss] = Field(default_factory=list)
    chips: list[ChipPlay] = Field(default_factory=list)
    chip_squad_changes: list[ChipSquadChange] = Field(default_factory=list)
    auto_subs: list[AutoSub] = Field(default_factory=list)


class TableRow(Strict):
    rank: int
    manager: str
    total_points: int
    movement: int | None


class TopThree(Strict):
    manager: str
    total_points: int
    behind_leader: int


class Table(Strict):
    rows: list[TableRow] = Field(default_factory=list)
    top3: list[TopThree] = Field(default_factory=list)
    climbers: list[str] = Field(default_factory=list)
    fallers: list[str] = Field(default_factory=list)


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
    net_points: int


class Season(Strict):
    rows: list[SeasonRow] = Field(default_factory=list)
    best_gameweek: list[Record] = Field(default_factory=list)
    worst_gameweek: list[Record] = Field(default_factory=list)


class FactSheet(Strict):
    version: Literal[1] = 1
    league: str
    season: str
    gameweek: int
    managers: int
    average_points: float
    winners: list[ManagerScore]
    flops: list[ManagerScore]
    captaincy: Captaincy = Field(default_factory=Captaincy)
    bench_transfers_chips: BenchTransfersChips = Field(default_factory=BenchTransfersChips)
    table: Table = Field(default_factory=Table)
    season_facts: Season = Field(default_factory=Season)
    empty_sections: list[str] = Field(default_factory=list)


def empty_sections(sheet: FactSheet) -> list[str]:
    extras = sheet.bench_transfers_chips
    empty = {
        "winners": not sheet.winners,
        "flops": not sheet.flops,
        "captaincy": not sheet.captaincy.picks,
        "bench_transfers_chips": not (
            extras.bench
            or extras.hits
            or extras.transfer_misses
            or extras.chips
            or extras.chip_squad_changes
            or extras.auto_subs
        ),
        "table": not sheet.table.rows,
    }
    return [section for section in SECTIONS if empty[section]]


def _managers_named(sheet: FactSheet) -> set[str]:
    names = {score.manager for score in sheet.winners + sheet.flops}
    names |= {pick.manager for pick in sheet.captaincy.picks}
    names |= set(sheet.captaincy.best) | set(sheet.captaincy.worst)
    extras = sheet.bench_transfers_chips
    for group in (
        extras.bench,
        extras.hits,
        extras.transfer_misses,
        extras.chips,
        extras.chip_squad_changes,
        extras.auto_subs,
    ):
        names |= {item.manager for item in group}
    names |= {row.manager for row in sheet.season_facts.rows}
    names |= set(sheet.table.climbers) | set(sheet.table.fallers)
    return names


def check_fact_sheet(sheet: FactSheet) -> list[str]:
    problems: list[str] = []
    for label, scores in (("winners", sheet.winners), ("flops", sheet.flops)):
        for score in scores:
            if score.net_points != score.points - score.transfers_cost:
                problems.append(f"{label}: net points do not match points and hits")
        if len({score.net_points for score in scores}) > 1:
            problems.append(f"{label}: managers on different net points")
    if sheet.winners and sheet.flops and sheet.winners[0].net_points < sheet.flops[0].net_points:
        problems.append("winners score below the flops")

    rows = sheet.table.rows
    if rows:
        totals = [row.total_points for row in rows]
        if totals != sorted(totals, reverse=True):
            problems.append("table: rows are not ordered by total points")
        for row in rows:
            expected = 1 + sum(total > row.total_points for total in totals)
            if row.rank != expected:
                problems.append("table: a rank does not match the points")
                break
        leader = rows[0].total_points
        for place, top in enumerate(sheet.table.top3):
            if place >= len(rows) or top.manager != rows[place].manager:
                problems.append("table: top3 does not match the table")
            elif top.behind_leader != leader - rows[place].total_points:
                problems.append("table: behind_leader does not match the table")
        if len(sheet.table.top3) != min(3, len(rows)):
            problems.append("table: top3 has the wrong length")
        in_table = {row.manager for row in rows}
        if len(in_table) != len(rows):
            problems.append("table: a manager appears twice")
        if sheet.managers != len(rows):
            problems.append("managers does not match the table")
        outside = _managers_named(sheet) - in_table
        if outside:
            problems.append("a section names a manager who is not in the table")

    for pick in sheet.captaincy.picks:
        if pick.points != pick.base_points * pick.multiplier:
            problems.append("captaincy: points do not match base points and multiplier")

    if sheet.empty_sections != empty_sections(sheet):
        problems.append("empty_sections does not match the sections")
    return problems
