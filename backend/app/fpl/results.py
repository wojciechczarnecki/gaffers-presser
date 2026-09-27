from datetime import datetime

from sqlmodel import Session, select

from app.db.upsert import upsert
from app.fpl.client import FplClient
from app.fpl.errors import JobError
from app.fpl.models import Gameweek, PlayerGameweekResult, RawPayload
from app.fpl.reference import apply_fixtures


def _resolve_gameweek(session: Session, gameweek: int) -> tuple[str, Gameweek]:
    row = session.exec(
        select(Gameweek).where(Gameweek.fpl_id == gameweek).order_by(Gameweek.deadline_at.desc())
    ).first()
    if row is None:
        raise JobError(f"gameweek {gameweek} does not exist")
    return row.season, row


def sync_results(session: Session, client: FplClient, gameweek: int, now: datetime) -> None:
    season, gw_row = _resolve_gameweek(session, gameweek)
    if not (gw_row.finished and gw_row.data_checked):
        raise JobError(f"gameweek {gameweek} is not finished and data-checked")

    live = client.live(gameweek)
    fixtures = client.fixtures()

    upsert(
        session,
        PlayerGameweekResult,
        [
            {
                "season": season,
                "player_fpl_id": el.id,
                "gameweek_fpl_id": gameweek,
                "starts": el.stats.starts,
                "minutes": el.stats.minutes,
                "total_points": el.stats.total_points,
                "explain": el.explain,
            }
            for el in live.data.elements
        ],
        ["season", "player_fpl_id", "gameweek_fpl_id"],
    )
    apply_fixtures(session, season, fixtures.data)

    session.add(
        RawPayload(
            season=season,
            endpoint=f"event/{gameweek}/live",
            gameweek_fpl_id=gameweek,
            fetched_at=now,
            payload=live.raw,
        )
    )
    session.add(
        RawPayload(
            season=season,
            endpoint="fixtures",
            gameweek_fpl_id=gameweek,
            fetched_at=now,
            payload=fixtures.raw,
        )
    )
