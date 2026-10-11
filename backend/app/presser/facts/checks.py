from decimal import Decimal

from app.presser.facts import ranks
from app.presser.facts.gameweek import BLANK_MAX_POINTS
from app.presser.facts.moments import MAX_KEY_MOMENTS
from app.presser.facts.pitch import MAX_PITCH_ITEMS
from app.presser.facts.schema import FactSheet, PlayerStats, empty_sections
from app.presser.facts.stats import BIG_MISS_MIN_XG, GOAL_FROM_NOTHING_BELOW_XG
from app.presser.facts.table import RIVALRY_MIN_NEIGHBOUR_GAMEWEEKS


def managers_named(sheet: FactSheet) -> set[str]:
    names = {score.manager for score in sheet.winners + sheet.flops}
    names |= {pick.manager for pick in sheet.captaincy.picks}
    names |= set(sheet.captaincy.best) | set(sheet.captaincy.worst)
    extras = sheet.bench_transfers
    for group in (
        extras.bench,
        extras.hits,
        extras.transfer_misses,
        extras.transfer_wins,
        extras.auto_subs,
        sheet.chips.plays,
        sheet.chips.squad_changes,
    ):
        names |= {item.manager for item in group}
    for item in sheet.pitch.items:
        names |= {owner.manager for owner in item.owners}
        names |= set(item.benched) | set(item.without)
    for moment in sheet.key_moments:
        names |= {moment.ahead, moment.behind}
    for swap in sheet.table.rivalry_swaps:
        names |= {swap.ahead, swap.behind}
    for change in sheet.table.podium_changes:
        names |= {change.manager}
        if change.other is not None:
            names.add(change.other)
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
        if item.ranked_gameweeks < ranks.MIN_RANKED_GAMEWEEKS:
            problems.append("personal ranks: too few ranked gameweeks")
    if best and any(item.gameweek_rank < min(best) for item in season.personal_bests):
        problems.append("personal ranks: a personal best beats the league best")
    if worst and any(item.gameweek_rank > max(worst) for item in season.personal_worsts):
        problems.append("personal ranks: a personal worst is below the league worst")
    return problems


def _check_table_stories(sheet: FactSheet) -> list[str]:
    problems: list[str] = []
    rows = {row.manager: row for row in sheet.table.rows}
    top3 = [top.manager for top in sheet.table.top3]
    for swap in sheet.table.rivalry_swaps:
        ahead, behind = rows.get(swap.ahead), rows.get(swap.behind)
        if (
            ahead is None
            or behind is None
            or swap.ahead_rank != ahead.rank
            or swap.behind_rank != behind.rank
            or swap.gap != ahead.total_points - behind.total_points
            or swap.gap <= 0
            or swap.neighbour_gameweeks < RIVALRY_MIN_NEIGHBOUR_GAMEWEEKS
            or sheet.gameweek <= RIVALRY_MIN_NEIGHBOUR_GAMEWEEKS
        ):
            problems.append("table: a rivalry swap does not match the table")
    for change in sheet.table.podium_changes:
        row = rows.get(change.manager)
        other = rows.get(change.other) if change.other is not None else None
        wrong = sheet.gameweek == 1 or row is None or change.rank != row.rank
        if change.kind == "new_leader":
            wrong = wrong or not top3 or change.manager != top3[0]
        elif change.kind == "left":
            wrong = wrong or change.manager in top3
        else:
            wrong = wrong or change.manager not in top3
        if row is not None and other is not None:
            wrong = wrong or change.gap != row.total_points - other.total_points
        elif change.gap is not None:
            wrong = True
        if wrong:
            problems.append("table: a podium change does not match the top 3")
    return problems


def _pair_exists(sheet: FactSheet, moment) -> bool:
    if moment.pair == "winner":
        winners = {score.manager for score in sheet.winners}
        return moment.ahead in winners and moment.behind not in winners
    if moment.pair == "rivalry":
        return any(
            (swap.ahead, swap.behind, swap.gap) == (moment.ahead, moment.behind, moment.margin)
            for swap in sheet.table.rivalry_swaps
        )
    return any(
        (change.manager, change.other, change.gap) == (moment.ahead, moment.behind, moment.margin)
        for change in sheet.table.podium_changes
    )


def _check_key_moments(sheet: FactSheet) -> list[str]:
    problems: list[str] = []
    if len(sheet.key_moments) > MAX_KEY_MOMENTS:
        problems.append("key moments: more than 5")
    winner_margins = {m.margin for m in sheet.key_moments if m.pair == "winner"}
    if len(winner_margins) > 1:
        problems.append("key moments: winner pairs on different margins")
    for moment in sheet.key_moments:
        if moment.margin <= 0:
            problems.append("key moments: a margin is not positive")
        if moment.swing < moment.margin:
            problems.append("key moments: a swing is below its margin")
        if moment.swing != moment.ahead_points - moment.behind_points or moment.swing <= 0:
            problems.append("key moments: a swing does not match the points")
        if not _pair_exists(sheet, moment):
            problems.append("key moments: a pair is not a winner, a swap or a podium change")
    return problems


def _check_player_stats(points: int, facts: PlayerStats) -> list[str]:
    problems: list[str] = []
    if facts.breakdown is not None and sum(entry.points for entry in facts.breakdown) != points:
        problems.append("breakdown: does not sum to the player's points")
    goals = 0
    if facts.breakdown is not None:
        goals = sum(e.value for e in facts.breakdown if e.identifier == "goals_scored")
    xg: Decimal | None = facts.expected_goals
    if xg is None:
        expected = (None, None)
    else:
        expected = (
            points <= BLANK_MAX_POINTS and xg >= BIG_MISS_MIN_XG,
            goals >= 1 and xg < GOAL_FROM_NOTHING_BELOW_XG,
        )
    if (facts.big_miss, facts.goal_from_nothing) != expected:
        problems.append("xG: a flag does not match the thresholds")
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
        outside = managers_named(sheet) - in_table
        if outside:
            problems.append("a section names a manager who is not in the table")

    for hit in sheet.bench_transfers.hits:
        gain = sum(pair.player_in_points - pair.player_out_points for pair in hit.pairs)
        if hit.transfer_gain != gain:
            problems.append("hits: a transfer gain does not match its transfers")
        if hit.paid_off != (hit.transfer_gain > hit.cost):
            problems.append("hits: paid_off does not match the gain and the cost")

    problems += _check_table_stories(sheet)
    problems += _check_key_moments(sheet)

    if len(sheet.pitch.items) > MAX_PITCH_ITEMS:
        problems.append("pitch: more than 8 items")

    for pick in sheet.captaincy.picks:
        if pick.points != pick.base_points * pick.multiplier:
            problems.append("captaincy: points do not match base points and multiplier")
        problems += _check_player_stats(pick.base_points, pick)
    for item in sheet.pitch.items:
        problems += _check_player_stats(item.points, item)

    if sheet.empty_sections != empty_sections(sheet):
        problems.append("empty_sections does not match the sections")
    return problems
