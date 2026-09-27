from datetime import datetime

from sqlmodel import Session, delete, select

from app.fpl.client import FplClient
from app.fpl.errors import JobError
from app.fpl.models import DeadlineSnapshotPlayer, Gameweek, RawPayload
from app.fpl.reference import apply_bootstrap


def take_deadline_snapshot(
    session: Session, client: FplClient, gameweek: int, now: datetime
) -> None:
    bootstrap = client.bootstrap()
    season = apply_bootstrap(session, bootstrap.data, now)

    gw_row = session.exec(
        select(Gameweek).where(Gameweek.season == season, Gameweek.fpl_id == gameweek)
    ).first()
    if gw_row is None:
        raise JobError(f"gameweek {gameweek} does not exist")
    if now >= gw_row.deadline_at:
        raise JobError(f"gameweek {gameweek} deadline has passed")

    session.exec(
        delete(DeadlineSnapshotPlayer).where(
            DeadlineSnapshotPlayer.season == season,
            DeadlineSnapshotPlayer.gameweek_fpl_id == gameweek,
        )
    )
    rows = [
        DeadlineSnapshotPlayer(
            season=season,
            gameweek_fpl_id=gameweek,
            player_fpl_id=el.id,
            status=el.status,
            news=el.news,
            chance_of_playing_this_round=el.chance_of_playing_this_round,
            chance_of_playing_next_round=el.chance_of_playing_next_round,
            selected_by_percent=el.selected_by_percent,
            now_cost=el.now_cost,
            captured_at=now,
        )
        for el in bootstrap.data.elements
    ]
    session.add_all(rows)
    session.add(
        RawPayload(
            season=season,
            endpoint="bootstrap-static",
            gameweek_fpl_id=gameweek,
            fetched_at=now,
            payload=bootstrap.raw,
        )
    )
