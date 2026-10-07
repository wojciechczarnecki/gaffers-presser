from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.errors import CollectorError
from app.tweets.models import ListMembership
from app.tweets.sources.base import MembershipNotSupportedError, SourcePayloadError, TweetSource

MEMBERSHIP_INTERVAL = timedelta(hours=6)
MEMBERSHIP_RETRY = timedelta(minutes=30)
# The owner curates the List a few accounts at a time; a larger drop at once is more likely a
# truncated fetch (twscrape ends paging silently) than a real change.
MAX_REMOVED_MEMBERS = 3


@dataclass(frozen=True)
class MembershipSnapshot:
    fetched_at: datetime
    source: str
    list_id: int
    handles: frozenset[str] | None


class MembershipShrunkError(CollectorError):
    def __init__(self, previous: int, current: int) -> None:
        super().__init__(f"list membership shrank from {previous} to {current} members")
        self.previous = previous
        self.current = current


def fetch_membership(source: TweetSource, list_id: int, now: datetime) -> MembershipSnapshot:
    handles: frozenset[str] | None
    try:
        members = source.members(list_id)
    except MembershipNotSupportedError:
        handles = None
    else:
        if not members:
            raise SourcePayloadError(f"{source.name}: empty member list")
        handles = frozenset(handle.lower() for handle in members)
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


def check_shrink(previous: MembershipSnapshot | None, snapshot: MembershipSnapshot) -> None:
    if previous is None or previous.handles is None or snapshot.handles is None:
        return
    if previous.list_id != snapshot.list_id:
        return
    if len(previous.handles - snapshot.handles) > MAX_REMOVED_MEMBERS:
        raise MembershipShrunkError(len(previous.handles), len(snapshot.handles))


def store_membership(engine: Engine, snapshot: MembershipSnapshot, force: bool = False) -> None:
    if not force:
        with Session(engine) as session:
            previous = latest_snapshot(session)
        check_shrink(previous, snapshot)
    save_snapshot(engine, snapshot)
