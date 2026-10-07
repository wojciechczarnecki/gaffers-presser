from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.fpl.models.reference import Gameweek

# how far before a deadline the alert window may reach (ALERT_MAX_LOOKBACK_DAYS)
DEFAULT_MAX_LOOKBACK = timedelta(days=7)


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


def window_floor(
    previous: datetime | None, next_: datetime | None, max_lookback: timedelta, now: datetime
) -> datetime:
    """The start of the alert window: the previous deadline, but no further back than
    max_lookback before the next deadline (or before now when no deadline is known). Shared by
    the alerts and the tweet ingest catch-up, so the ingest always covers the alert window."""
    candidates = [now - max_lookback if next_ is None else next_ - max_lookback]
    if previous is not None:
        candidates.append(previous)
    return max(candidates)
