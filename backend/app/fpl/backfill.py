from datetime import datetime

from sqlmodel import Session, select

from app.fpl.client import FplClient
from app.fpl.leagues import sync_leagues
from app.fpl.models import Gameweek
from app.fpl.reference import sync_reference
from app.fpl.results import sync_results


def backfill(session: Session, client: FplClient, league_ids: list[int], now: datetime) -> None:
    season = sync_reference(session, client, now)
    gameweeks = session.exec(
        select(Gameweek).where(Gameweek.season == season).order_by(Gameweek.fpl_id)
    ).all()

    passed = [gw.fpl_id for gw in gameweeks if gw.deadline_at <= now]
    if passed:
        sync_leagues(session, client, league_ids, passed, now)

    for gw in gameweeks:
        if gw.finished and gw.data_checked:
            sync_results(session, client, gw.fpl_id, now)
