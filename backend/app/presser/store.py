from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func
from sqlmodel import Session, col, select

from app.fpl.models import Season
from app.presser.models import Presser
from app.presser.writer import PreviousPresser


def current_season(session: Session) -> str | None:
    return session.exec(select(Season.label).order_by(col(Season.label).desc())).first()


def insert_presser(engine: Engine, **fields: Any) -> int:
    with Session(engine) as session:
        row = Presser(**fields)
        session.add(row)
        session.commit()
        assert row.id is not None
        return row.id


def update_presser(engine: Engine, presser_id: int, **fields: Any) -> None:
    with Session(engine) as session:
        row = session.get(Presser, presser_id)
        assert row is not None
        for name, value in fields.items():
            setattr(row, name, value)
        session.add(row)
        session.commit()


def previous_pressers(
    session: Session, season: str, league_id: int, gameweek: int, limit: int = 2
) -> list[PreviousPresser]:
    rows = session.exec(
        select(Presser)
        .where(
            Presser.season == season,
            Presser.league_fpl_id == league_id,
            Presser.gameweek_fpl_id < gameweek,
            Presser.status == "sent",
            col(Presser.text).is_not(None),
        )
        .order_by(col(Presser.gameweek_fpl_id).desc(), col(Presser.created_at).desc())
    ).all()
    latest: dict[int, str] = {}
    for row in rows:
        if row.gameweek_fpl_id not in latest and row.text is not None:
            latest[row.gameweek_fpl_id] = row.text
        if len(latest) == limit:
            break
    return [PreviousPresser(number, latest[number]) for number in sorted(latest)]


def key_has_status(session: Session, key: str, statuses: tuple[str, ...]) -> bool:
    return (
        session.exec(
            select(Presser.id)
            .where(Presser.idempotency_key == key, col(Presser.status).in_(statuses))
            .limit(1)
        ).first()
        is not None
    )


@dataclass(frozen=True)
class LatestPresser:
    gameweek: int
    status: str
    created_at: datetime
    model: str
    cost_usd: float | None


@dataclass(frozen=True)
class LeaguePresserStatus:
    league_id: int
    latest: LatestPresser | None
    failed: int


def presser_status(
    session: Session, season: str, league_ids: list[int]
) -> list[LeaguePresserStatus]:
    statuses = []
    for league_id in league_ids:
        latest = session.exec(
            select(Presser)
            .where(Presser.season == season, Presser.league_fpl_id == league_id)
            .order_by(col(Presser.created_at).desc(), col(Presser.id).desc())
            .limit(1)
        ).first()
        failed = session.exec(
            select(func.count())
            .select_from(Presser)
            .where(
                Presser.season == season,
                Presser.league_fpl_id == league_id,
                Presser.status == "failed",
            )
        ).one()
        statuses.append(
            LeaguePresserStatus(
                league_id=league_id,
                latest=None
                if latest is None
                else LatestPresser(
                    gameweek=latest.gameweek_fpl_id,
                    status=latest.status,
                    created_at=latest.created_at,
                    model=latest.model,
                    cost_usd=latest.cost_usd,
                ),
                failed=failed,
            )
        )
    return statuses
