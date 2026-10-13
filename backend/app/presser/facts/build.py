from sqlmodel import Session

from app.presser.facts import gameweek as gw_rules
from app.presser.facts.checks import check_fact_sheet
from app.presser.facts.chips import build_chips
from app.presser.facts.errors import FactSheetError, NoFactsError
from app.presser.facts.load import (
    CaptainRef,
    load_auto_subs,
    load_bench_picks,
    load_captain_refs,
    load_club_fixture_counts,
    load_club_names,
    load_fixture_bonus,
    load_fixtures,
    load_league_name,
    load_manager_names,
    load_match_results,
    load_member_rows,
    load_ownership,
    load_player_info,
    load_player_names,
    load_player_positions,
    load_results,
    load_squads,
    load_team_names,
    load_transfers,
)
from app.presser.facts.matches import contributions, fixture_facts
from app.presser.facts.moments import build_key_moments
from app.presser.facts.overall import build_overall
from app.presser.facts.pitch import build_pitch
from app.presser.facts.schema import FactSheet, empty_sections
from app.presser.facts.season import build_season
from app.presser.facts.stats import bonus_near_misses, player_stat_facts
from app.presser.facts.table import build_table, podium_changes, rivalry_swaps
from app.presser.names import display_names


def build_fact_sheet(
    session: Session,
    season: str,
    league_id: int,
    gameweek: int,
    nicknames: dict[int, str],
    team_names: dict[int, str] | None = None,
) -> FactSheet:
    league_name = load_league_name(session, season, league_id)
    if league_name is None:
        raise NoFactsError("league not found")
    names = display_names(load_manager_names(session, season, league_id), nicknames)
    gameweeks = range(1, gameweek + 1)
    all_rows = load_member_rows(session, season, league_id, gameweeks)
    rows = [row for row in all_rows if row.gameweek == gameweek]
    if not rows:
        raise NoFactsError("no managers with a team in the gameweek")
    refs = load_captain_refs(session, season, league_id, gameweeks)
    bench_picks = load_bench_picks(session, season, league_id, gameweek)
    transfers = load_transfers(session, season, league_id, gameweek)
    auto_subs = load_auto_subs(session, season, league_id, gameweek)
    chips_by_gameweek = {(row.entry_id, row.gameweek): row.active_chip for row in all_rows}
    squad_chip_entries = {row.entry_id for row in rows if row.active_chip in gw_rules.SQUAD_CHIPS}
    before_chip = {
        entry_id: gw_rules.gameweek_before_chip(entry_id, gameweek, chips_by_gameweek)
        for entry_id in squad_chip_entries
    }
    squads = load_squads(
        session,
        season,
        {(row.entry_id, gameweek) for row in rows}
        | {(entry_id, number) for entry_id, number in before_chip.items() if number is not None},
    )
    previous_squads = {
        entry_id: squads.get((entry_id, number)) if number is not None else None
        for entry_id, number in before_chip.items()
    }
    squad_players = {pick.player for squad in squads.values() for pick in squad}
    player_ids = {
        player for ref in refs.values() for player in (ref.captain, ref.vice) if player is not None
    }
    player_ids |= {player for players in bench_picks.values() for player in players}
    player_ids |= {t.player_in for t in transfers} | {t.player_out for t in transfers}
    player_ids |= {s.player_in for s in auto_subs} | {s.player_out for s in auto_subs}
    player_ids |= squad_players
    results = load_results(session, season, gameweeks, player_ids)
    player_names = load_player_names(session, season, player_ids)
    positions = load_player_positions(session, season, squad_players)
    match_results = load_match_results(session, season, gameweek, squad_players)
    player_info = load_player_info(session, season, squad_players)
    fixture_ids = {item.fixture_id for result in match_results.values() for item in result.fixtures}
    fixtures = load_fixtures(session, season, fixture_ids)
    near_misses = bonus_near_misses(load_fixture_bonus(session, season, fixture_ids))
    facts = fixture_facts(match_results, player_info, fixtures)
    player_facts = {
        player_id: player_stat_facts(result) for player_id, result in match_results.items()
    }
    gameweek_squads = {
        row.entry_id: squads[(row.entry_id, gameweek)]
        for row in rows
        if (row.entry_id, gameweek) in squads
    }
    subs_by_entry: dict[int, list] = {}
    for sub in auto_subs:
        subs_by_entry.setdefault(sub.entry_id, []).append(sub)
    chips_by_entry = {row.entry_id: row.active_chip for row in rows}
    manager_contributions = {
        entry_id: contributions(
            squad,
            subs_by_entry.get(entry_id, []),
            refs.get((entry_id, gameweek), CaptainRef(None, None)),
            chips_by_entry[entry_id],
            results,
            gameweek,
        )
        for entry_id, squad in gameweek_squads.items()
    }

    season_facts = build_season(all_rows, refs, results, names, gameweek)
    winners, flops = gw_rules.winners_and_flops(rows, names, season_facts, gameweek)
    average = gw_rules.average_net_points(rows)
    sheet = FactSheet(
        league=league_name,
        season=season,
        gameweek=gameweek,
        managers=len(rows),
        average_net_points=average,
        winners=winners,
        flops=flops,
        captaincy=gw_rules.captaincy(
            rows, refs, results, names, player_names, gameweek, player_facts
        ),
        bench_transfers=gw_rules.build_bench_transfers(
            rows, bench_picks, transfers, auto_subs, results, names, player_names, gameweek
        ),
        chips=build_chips(
            rows,
            bench_picks,
            auto_subs,
            refs,
            results,
            names,
            player_names,
            gameweek,
            previous_squads,
            squads,
            positions,
        ),
    )
    previous = [row for row in all_rows if row.gameweek == gameweek - 1]
    team_names = team_names or load_team_names(session, season, league_id)
    sheet.table = build_table(rows, previous, names, team_names)
    teams = {row.manager: row.team_name for row in sheet.table.rows}
    for score in [*sheet.winners, *sheet.flops]:
        score.team_name = teams.get(score.manager)
    sheet.table.rivalry_swaps = rivalry_swaps(all_rows, names, gameweek)
    sheet.table.podium_changes = podium_changes(all_rows, names, gameweek)
    club_names = load_club_names(session, season)
    sheet.key_moments = build_key_moments(
        rows,
        names,
        sheet.table,
        manager_contributions,
        match_results,
        player_info,
        club_names,
        facts,
        player_facts,
        near_misses,
        load_club_fixture_counts(session, season, gameweek),
    )
    sheet.pitch = build_pitch(
        {row.entry_id: names[row.entry_id] for row in rows},
        gameweek_squads,
        manager_contributions,
        match_results,
        player_info,
        club_names,
        facts,
        load_ownership(session, season, gameweek),
        {player for moment in sheet.key_moments for player in moment.players},
        len(rows),
        player_facts,
    )
    sheet.overall = build_overall(rows, previous, names, gameweek)
    sheet.season_facts = season_facts
    sheet.empty_sections = empty_sections(sheet)
    if check_fact_sheet(sheet):
        raise FactSheetError("the fact sheet is internally inconsistent")
    return sheet
