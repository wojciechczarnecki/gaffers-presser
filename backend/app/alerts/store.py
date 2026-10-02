from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from app.alerts.models import Alert, AlertPost
from app.alerts.schemas import AlertDeadline, AlertKind
from app.tweets.reposts import origin_ids

COUNTED_STATUSES = ("sent", "failed")


@dataclass(frozen=True)
class IncludedPost:
    player_season: str
    player_fpl_id: int
    tweet_x_id: int
    freshness: str  # new | context


@dataclass(frozen=True)
class AlertRow:
    id: int
    key: str
    kind: str
    slot_minutes: int | None
    as_of: datetime
    status: str
    recorded_at: datetime
    delivery_log_id: int | None
    trigger_x_id: int | None


def record_alert(
    engine: Engine,
    *,
    key: str,
    deadline: AlertDeadline,
    kind: AlertKind,
    slot_minutes: int | None,
    trigger_x_id: int | None,
    as_of: datetime,
    status: str,
    delivery_log_id: int | None,
    posts: list[IncludedPost],
    recorded_at: datetime,
) -> int | None:
    stmt = (
        insert(Alert.__table__)
        .values(
            key=key,
            deadline_key=deadline.key,
            deadline_at=deadline.deadline_at,
            kind=kind,
            slot_minutes=slot_minutes,
            trigger_x_id=trigger_x_id,
            as_of=as_of,
            status=status,
            delivery_log_id=delivery_log_id,
            recorded_at=recorded_at,
        )
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(Alert.__table__.c.id)
    )
    with engine.begin() as conn:
        alert_id = conn.execute(stmt).scalar_one_or_none()
        if alert_id is None:
            return None
        unique = {(p.player_season, p.player_fpl_id, p.tweet_x_id): p for p in posts}
        if unique:
            conn.execute(
                AlertPost.__table__.insert(),
                [
                    {
                        "alert_id": alert_id,
                        "player_season": p.player_season,
                        "player_fpl_id": p.player_fpl_id,
                        "tweet_x_id": p.tweet_x_id,
                        "freshness": p.freshness,
                    }
                    for p in unique.values()
                ],
            )
        return alert_id


def _row(row) -> AlertRow:
    return AlertRow(
        id=row.id,
        key=row.key,
        kind=row.kind,
        slot_minutes=row.slot_minutes,
        as_of=row.as_of,
        status=row.status,
        recorded_at=row.recorded_at,
        delivery_log_id=row.delivery_log_id,
        trigger_x_id=row.trigger_x_id,
    )


_COLUMNS = "id, key, kind, slot_minutes, as_of, status, recorded_at, delivery_log_id, trigger_x_id"


def alert_exists(session: Session, key: str) -> bool:
    return (
        session.execute(text("SELECT 1 FROM alert WHERE key = :key"), {"key": key}).first()
        is not None
    )


def done_slots(session: Session, deadline_key: str) -> dict[int, AlertRow]:
    rows = session.execute(
        text(
            f"SELECT {_COLUMNS} FROM alert"
            " WHERE deadline_key = :deadline_key AND slot_minutes IS NOT NULL"
            " ORDER BY recorded_at, id"
        ),
        {"deadline_key": deadline_key},
    )
    return {row.slot_minutes: _row(row) for row in rows}


def included_origins(
    session: Session, deadline_key: str, before: datetime | None = None
) -> set[int]:
    query = (
        "SELECT DISTINCT ON (t.x_id) t.x_id, t.raw FROM alert a"
        " JOIN alert_post ap ON ap.alert_id = a.id"
        " JOIN tweet t ON t.x_id = ap.tweet_x_id"
        " WHERE a.deadline_key = :deadline_key AND a.status = ANY(:statuses)"
        " AND (a.kind <> 'breaking' OR ap.freshness = 'new')"
    )
    params: dict[str, object] = {
        "deadline_key": deadline_key,
        "statuses": list(COUNTED_STATUSES),
    }
    if before is not None:
        query += " AND a.as_of < :before"
        params["before"] = before
    included: set[int] = set()
    for x_id, raw in session.execute(text(query), params):
        included |= origin_ids(x_id, raw)
    return included


def status_rows(session: Session, deadline_key: str) -> tuple[AlertRow | None, int]:
    last = session.execute(
        text(
            f"SELECT {_COLUMNS} FROM alert WHERE deadline_key = :deadline_key"
            " ORDER BY recorded_at DESC, id DESC LIMIT 1"
        ),
        {"deadline_key": deadline_key},
    ).first()
    failed = session.execute(
        text("SELECT count(*) FROM alert WHERE deadline_key = :deadline_key AND status = 'failed'"),
        {"deadline_key": deadline_key},
    ).scalar_one()
    return (_row(last) if last is not None else None, failed)


def post_origin_sets(session: Session, x_ids: list[int]) -> dict[int, set[int]]:
    if not x_ids:
        return {}
    rows = session.execute(
        text("SELECT x_id, raw FROM tweet WHERE x_id = ANY(:ids)"), {"ids": list(x_ids)}
    )
    return {x_id: origin_ids(x_id, raw) for x_id, raw in rows}


def last_alert_as_of(session: Session, deadline_key: str, before: datetime | None = None):
    query = (
        "SELECT max(as_of) FROM alert WHERE deadline_key = :deadline_key"
        " AND status = ANY(:statuses)"
    )
    params: dict[str, object] = {
        "deadline_key": deadline_key,
        "statuses": list(COUNTED_STATUSES),
    }
    if before is not None:
        query += " AND as_of < :before"
        params["before"] = before
    return session.execute(text(query), params).scalar_one()
