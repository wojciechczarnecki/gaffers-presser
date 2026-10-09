from sqlmodel import Session

from app.presser.facts import gameweek as gw_rules
from app.presser.facts.errors import FactSheetError, NoFactsError
from app.presser.facts.load import (
    load_auto_subs,
    load_bench_picks,
    load_captain_refs,
    load_league_name,
    load_manager_names,
    load_member_rows,
    load_player_names,
    load_player_positions,
    load_results,
    load_squads,
    load_transfers,
)
from app.presser.facts.schema import FactSheet, check_fact_sheet, empty_sections
from app.presser.facts.season import build_season
from app.presser.facts.table import build_table
from app.presser.names import display_names


def build_fact_sheet(
    session: Session, season: str, league_id: int, gameweek: int, nicknames: dict[int, str]
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
        {(entry_id, gameweek) for entry_id in squad_chip_entries}
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

    season_facts = build_season(all_rows, refs, results, names, gameweek)
    winners, flops = gw_rules.winners_and_flops(rows, names, season_facts)
    average = gw_rules.average_points(rows)
    sheet = FactSheet(
        league=league_name,
        season=season,
        gameweek=gameweek,
        managers=len(rows),
        average_points=average,
        winners=winners,
        flops=flops,
        captaincy=gw_rules.captaincy(rows, refs, results, names, player_names, gameweek),
        bench_transfers_chips=gw_rules.bench_transfers_chips(
            rows,
            bench_picks,
            transfers,
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
    sheet.table = build_table(rows, previous, names)
    sheet.season_facts = season_facts
    sheet.empty_sections = empty_sections(sheet)
    if check_fact_sheet(sheet):
        raise FactSheetError("the fact sheet is internally inconsistent")
    return sheet
