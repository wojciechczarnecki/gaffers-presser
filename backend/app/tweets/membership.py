from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.tweets.models import ListMembership
from app.tweets.sources.base import MembershipNotSupportedError, TweetSource

MEMBERSHIP_INTERVAL = timedelta(hours=6)
MEMBERSHIP_RETRY = timedelta(minutes=30)


@dataclass(frozen=True)
class MembershipSnapshot:
    fetched_at: datetime
    source: str
    list_id: int
    handles: frozenset[str] | None


def fetch_membership(source: TweetSource, list_id: int, now: datetime) -> MembershipSnapshot:
    try:
        handles: frozenset[str] | None = frozenset(
            handle.lower() for handle in source.members(list_id)
        )
    except MembershipNotSupportedError:
        handles = None
    return MembershipSnapshot(fetched_at=now, source=source.name, list_id=list_id, handles=handles)


def save_snapshot(engine: Engine, snapshot: MembershipSnapshot) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            ListMembership(
                list_id=snapshot.list_id,
                source=snapshot.source,
                fetched_at=snapshot.fetched_at,
                handles=sorted(snapshot.handles) if snapshot.handles is not None else None,
            )
        )


def latest_snapshot(session: Session) -> MembershipSnapshot | None:
    row = session.exec(
        select(ListMembership).order_by(ListMembership.fetched_at.desc(), ListMembership.id.desc())
    ).first()
    if row is None:
        return None
    return MembershipSnapshot(
        fetched_at=row.fetched_at,
        source=row.source,
        list_id=row.list_id,
        handles=frozenset(row.handles) if row.handles is not None else None,
    )


def refresh_membership(
    engine: Engine, source: TweetSource, list_id: int, now: datetime
) -> MembershipSnapshot:
    snapshot = fetch_membership(source, list_id, now)
    save_snapshot(engine, snapshot)
    return snapshot
