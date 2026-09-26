import logging
from datetime import datetime

from sqlmodel import Session, delete, select

from app.db.upsert import upsert
from app.fpl.client import FplClient
from app.fpl.errors import FplNotFoundError, JobError
from app.fpl.models import (
    Gameweek,
    League,
    LeagueMembership,
    LeagueStanding,
    Manager,
    ManagerAutoSub,
    ManagerChip,
    ManagerGameweek,
    ManagerPick,
    ManagerTransfer,
)

logger = logging.getLogger(__name__)


def _resolve_season(session: Session, gameweeks: list[int]) -> str:
    row = session.exec(
        select(Gameweek.season)
        .where(Gameweek.fpl_id.in_(gameweeks))
        .order_by(Gameweek.deadline_at.desc())
    ).first()
    if row is None:
        raise JobError("gameweek data is missing; run reference-sync first")
    return row


def _ensure_gameweeks_exist(session: Session, season: str, gameweeks: list[int]) -> None:
    existing = set(
        session.exec(
            select(Gameweek.fpl_id).where(Gameweek.season == season, Gameweek.fpl_id.in_(gameweeks))
        ).all()
    )
    missing = sorted(set(gameweeks) - existing)
    if missing:
        raise JobError(f"gameweek {missing[0]} does not exist")


def _ensure_deadlines_passed(
    session: Session, season: str, gameweeks: list[int], now: datetime
) -> None:
    rows = session.exec(
        select(Gameweek).where(Gameweek.season == season, Gameweek.fpl_id.in_(gameweeks))
    ).all()
    for row in rows:
        if row.deadline_at > now:
            raise JobError(f"gameweek {row.fpl_id} deadline has not passed")


def _latest_passed_gameweek(session: Session, season: str, now: datetime) -> int:
    row = session.exec(
        select(Gameweek)
        .where(Gameweek.season == season, Gameweek.deadline_at <= now)
        .order_by(Gameweek.fpl_id.desc())
    ).first()
    if row is None:
        raise JobError("no gameweek deadline has passed yet")
    return row.fpl_id


def _sync_league_standings(
    session: Session, client: FplClient, league_id: int, season: str, latest_gw: int
) -> set[int]:
    page = 1
    results = []
    league_name = None
    while True:
        page_data = client.league_standings(league_id, page)
        league_name = page_data.league.name
        results.extend(page_data.standings.results)
        if not page_data.standings.has_next:
            break
        page += 1

    upsert(
        session,
        League,
        [{"season": season, "fpl_id": league_id, "name": league_name}],
        ["season", "fpl_id"],
    )
    upsert(
        session,
        Manager,
        [
            {
                "season": season,
                "entry_id": r.entry,
                "team_name": r.entry_name,
                "manager_name": r.player_name,
            }
            for r in results
        ],
        ["season", "entry_id"],
    )
    upsert(
        session,
        LeagueMembership,
        [{"season": season, "league_fpl_id": league_id, "entry_id": r.entry} for r in results],
        ["season", "league_fpl_id", "entry_id"],
    )
    upsert(
        session,
        LeagueStanding,
        [
            {
                "season": season,
                "league_fpl_id": league_id,
                "gameweek_fpl_id": latest_gw,
                "entry_id": r.entry,
                "rank": r.rank,
                "event_total": r.event_total,
                "total": r.total,
            }
            for r in results
        ],
        ["season", "league_fpl_id", "gameweek_fpl_id", "entry_id"],
    )
    return {r.entry for r in results}


def _no_team_gameweek(season: str, entry_id: int, gw: int) -> dict:
    return {
        "season": season,
        "entry_id": entry_id,
        "gameweek_fpl_id": gw,
        "has_team": False,
        "active_chip": None,
        "points": None,
        "total_points": None,
        "event_transfers": None,
        "event_transfers_cost": None,
        "points_on_bench": None,
        "bank": None,
        "value": None,
        "overall_rank": None,
    }


def _sync_entry(
    session: Session, client: FplClient, season: str, entry_id: int, gameweeks: list[int]
) -> None:
    transfers = client.entry_transfers(entry_id)
    upsert(
        session,
        ManagerTransfer,
        [
            {
                "season": season,
                "entry_id": entry_id,
                "made_at": t.time,
                "player_in_fpl_id": t.element_in,
                "gameweek_fpl_id": t.event,
                "player_out_fpl_id": t.element_out,
                "player_in_cost": t.element_in_cost,
                "player_out_cost": t.element_out_cost,
            }
            for t in transfers
        ],
        ["season", "entry_id", "made_at", "player_in_fpl_id"],
    )

    history = client.entry_history(entry_id)
    upsert(
        session,
        ManagerChip,
        [
            {
                "season": season,
                "entry_id": entry_id,
                "name": c.name,
                "gameweek_fpl_id": c.event,
                "played_at": c.time,
            }
            for c in history.chips
        ],
        ["season", "entry_id", "name", "gameweek_fpl_id"],
    )

    for gw in gameweeks:
        session.exec(
            delete(ManagerPick).where(
                ManagerPick.season == season,
                ManagerPick.entry_id == entry_id,
                ManagerPick.gameweek_fpl_id == gw,
            )
        )
        session.exec(
            delete(ManagerAutoSub).where(
                ManagerAutoSub.season == season,
                ManagerAutoSub.entry_id == entry_id,
                ManagerAutoSub.gameweek_fpl_id == gw,
            )
        )
        try:
            picks = client.entry_picks(entry_id, gw)
        except FplNotFoundError:
            upsert(
                session,
                ManagerGameweek,
                [_no_team_gameweek(season, entry_id, gw)],
                ["season", "entry_id", "gameweek_fpl_id"],
            )
            continue

        eh = picks.entry_history
        upsert(
            session,
            ManagerGameweek,
            [
                {
                    "season": season,
                    "entry_id": entry_id,
                    "gameweek_fpl_id": gw,
                    "has_team": True,
                    "active_chip": picks.active_chip,
                    "points": eh.points,
                    "total_points": eh.total_points,
                    "event_transfers": eh.event_transfers,
                    "event_transfers_cost": eh.event_transfers_cost,
                    "points_on_bench": eh.points_on_bench,
                    "bank": eh.bank,
                    "value": eh.value,
                    "overall_rank": eh.overall_rank,
                }
            ],
            ["season", "entry_id", "gameweek_fpl_id"],
        )
        if picks.picks:
            session.add_all(
                [
                    ManagerPick(
                        season=season,
                        entry_id=entry_id,
                        gameweek_fpl_id=gw,
                        position=p.position,
                        player_fpl_id=p.element,
                        multiplier=p.multiplier,
                        is_captain=p.is_captain,
                        is_vice_captain=p.is_vice_captain,
                    )
                    for p in picks.picks
                ]
            )
        if picks.automatic_subs:
            session.add_all(
                [
                    ManagerAutoSub(
                        season=season,
                        entry_id=entry_id,
                        gameweek_fpl_id=gw,
                        player_out_fpl_id=s.element_out,
                        player_in_fpl_id=s.element_in,
                    )
                    for s in picks.automatic_subs
                ]
            )


def sync_leagues(
    session: Session,
    client: FplClient,
    league_ids: list[int],
    gameweeks: list[int],
    now: datetime,
) -> None:
    season = _resolve_season(session, gameweeks)
    _ensure_gameweeks_exist(session, season, gameweeks)
    _ensure_deadlines_passed(session, season, gameweeks, now)
    latest_gw = _latest_passed_gameweek(session, season, now)

    all_entries: set[int] = set()
    for league_id in league_ids:
        all_entries |= _sync_league_standings(session, client, league_id, season, latest_gw)

    for entry_id in sorted(all_entries):
        _sync_entry(session, client, season, entry_id, gameweeks)

    logger.info(
        "league sync: leagues=%d gameweeks=%d managers=%d",
        len(league_ids),
        len(gameweeks),
        len(all_entries),
    )
