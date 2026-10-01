from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.fpl.models.reference import Gameweek


def _identity(item: Any) -> Any:
    return item


def next_deadline_after[T](
    items: Iterable[T], t: datetime, key: Callable[[T], datetime] = _identity
) -> T | None:
    candidates = [item for item in items if key(item) > t]
    return min(candidates, key=key) if candidates else None


def latest_deadline_at_or_before[T](
    items: Iterable[T], t: datetime, key: Callable[[T], datetime] = _identity
) -> T | None:
    candidates = [item for item in items if key(item) <= t]
    return max(candidates, key=key) if candidates else None


def upcoming_deadlines(session: Session, now: datetime) -> list[datetime]:
    rows = session.exec(
        select(Gameweek.deadline_at).where(Gameweek.deadline_at > now - timedelta(days=1))
    ).all()
    return sorted(rows)


def deadline_at_or_before(session: Session, t: datetime) -> datetime | None:
    return session.exec(
        select(func.max(Gameweek.deadline_at)).where(Gameweek.deadline_at <= t)
    ).one()
