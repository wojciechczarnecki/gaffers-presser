from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.presser.facts import ranks

SECTIONS = ("winners", "flops", "captaincy", "bench_transfers_chips", "table", "overall")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagerScore(Strict):
    manager: str
    transfers_cost: int
    net_points: int
    # this gameweek's win (in `winners`) or flop (in `flops`) is the Nth of the season
    nth_of_season: int
    gameweek_rank: int | None = None


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
    version: Literal[2] = 2
    league: str
    season: str
    gameweek: int
    managers: int
    average_net_points: int
    winners: list[ManagerScore]
    flops: list[ManagerScore]
    captaincy: Captaincy = Field(default_factory=Captaincy)
    bench_transfers_chips: BenchTransfersChips = Field(default_factory=BenchTransfersChips)
    table: Table = Field(default_factory=Table)
    overall: Overall = Field(default_factory=Overall)
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
        "overall": not any(row.notable for row in sheet.overall.rows),
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
    names |= {row.manager for row in sheet.overall.rows}
    names |= set(sheet.overall.biggest_climbers) | set(sheet.overall.biggest_fallers)
    names |= {item.manager for item in sheet.season_facts.personal_bests}
    names |= {item.manager for item in sheet.season_facts.personal_worsts}
    names |= {record.manager for record in sheet.season_facts.best_gameweek}
    names |= {record.manager for record in sheet.season_facts.worst_gameweek}
    return names


def _check_ranks(sheet: FactSheet) -> list[str]:
    problems: list[str] = []
    season = sheet.season_facts
    scores = sheet.winners + sheet.flops
    known = [score.gameweek_rank for score in scores if score.gameweek_rank is not None]
    positive = known + [r.gameweek_rank for r in season.best_gameweek + season.worst_gameweek]
    positive += [p.gameweek_rank for p in season.personal_bests + season.personal_worsts]
    for row in sheet.overall.rows:
        positive.append(row.overall_rank)
        if row.previous_overall_rank is not None:
            positive.append(row.previous_overall_rank)
    if any(rank <= 0 for rank in positive):
        problems.append("a rank is not positive")

    first = sheet.gameweek == 1
    moves = []
    for row in sheet.overall.rows:
        now, before = row.overall_rank, row.previous_overall_rank
        if now <= 0 or (before is not None and before <= 0):
            continue
        if row.movement != (None if before is None else before - now):
            problems.append("overall: a movement does not match the ranks")
        if row.entered != ranks.thresholds_entered(now, before, first):
            problems.append("overall: entered thresholds do not match the ranks")
        if row.left != ranks.thresholds_left(now, before):
            problems.append("overall: left thresholds do not match the ranks")
        if row.notable != ranks.is_notable(now, before, first):
            problems.append("overall: notable does not match the ranks")
        if row.notable and before is not None:
            moves.append((row.manager, now, before))
    if sheet.overall.biggest_climbers != ranks.leaders(moves, rising=True):
        problems.append("overall: biggest climbers do not match the rows")
    if sheet.overall.biggest_fallers != ranks.leaders(moves, rising=False):
        problems.append("overall: biggest fallers do not match the rows")

    best = {record.gameweek_rank for record in season.best_gameweek}
    worst = {record.gameweek_rank for record in season.worst_gameweek}
    if len(best) > 1 or len(worst) > 1:
        problems.append("records: tied records on different ranks")
    if best and worst and min(best) > max(worst):
        problems.append("records: the best rank is worse than the worst")
    if any(r.gameweek > sheet.gameweek for r in season.best_gameweek + season.worst_gameweek):
        problems.append("records: a record is from a later gameweek")
    if known and not (best and worst):
        problems.append("records: a known gameweek rank without season records")
    if best and worst and any(not min(best) <= rank <= max(worst) for rank in known):
        problems.append("records: a gameweek rank lies outside the season records")
    for item in season.personal_bests + season.personal_worsts:
        if item.ranked_gameweeks < 3:
            problems.append("personal ranks: fewer than 3 ranked gameweeks")
    if best and any(item.gameweek_rank < min(best) for item in season.personal_bests):
        problems.append("personal ranks: a personal best beats the league best")
    if worst and any(item.gameweek_rank > max(worst) for item in season.personal_worsts):
        problems.append("personal ranks: a personal worst is below the league worst")
    return problems


def check_fact_sheet(sheet: FactSheet) -> list[str]:
    problems: list[str] = _check_ranks(sheet)
    for label, scores in (("winners", sheet.winners), ("flops", sheet.flops)):
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
