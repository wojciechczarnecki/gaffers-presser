from sqlmodel import Session

from app.presser.facts import gameweek as gw_rules
from app.presser.facts.errors import NoFactsError
from app.presser.facts.load import (
    load_auto_subs,
    load_bench_picks,
    load_captain_refs,
    load_league_name,
    load_manager_names,
    load_member_rows,
    load_player_names,
    load_results,
    load_transfers,
)
from app.presser.facts.schema import FactSheet, empty_sections
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
    player_ids = {
        player for ref in refs.values() for player in (ref.captain, ref.vice) if player is not None
    }
    player_ids |= {player for players in bench_picks.values() for player in players}
    player_ids |= {t.player_in for t in transfers} | {t.player_out for t in transfers}
    player_ids |= {s.player_in for s in auto_subs} | {s.player_out for s in auto_subs}
    results = load_results(session, season, gameweeks, player_ids)
    player_names = load_player_names(session, season, player_ids)

    winners, flops = gw_rules.winners_and_flops(rows, names)
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
            average,
            gameweek,
        ),
    )
    sheet.empty_sections = empty_sections(sheet)
    return sheet
