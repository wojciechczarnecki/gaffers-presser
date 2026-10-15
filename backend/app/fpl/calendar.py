from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.fpl.models import Gameweek, Season


@dataclass(frozen=True)
class GameweekState:
    fpl_id: int
    deadline_at: datetime
    finished: bool
    data_checked: bool


@dataclass(frozen=True)
class Calendar:
    season: str | None
    gameweeks: list[GameweekState]


def current_season(session: Session) -> str | None:
    return session.exec(select(Season.label).order_by(col(Season.label).desc())).first()


def _season_gameweeks(session: Session, season: str) -> list[GameweekState]:
    rows = session.exec(
        select(Gameweek).where(Gameweek.season == season).order_by(Gameweek.fpl_id)
    ).all()
    return [
        GameweekState(
            fpl_id=row.fpl_id,
            deadline_at=row.deadline_at,
            finished=row.finished,
            data_checked=row.data_checked,
        )
        for row in rows
    ]


def read_calendar(session: Session) -> Calendar:
    season = current_season(session)
    gameweeks = _season_gameweeks(session, season) if season is not None else []
    return Calendar(season=season, gameweeks=gameweeks)


def load_calendar(engine: Engine) -> Calendar:
    with Session(engine) as session:
        return read_calendar(session)
