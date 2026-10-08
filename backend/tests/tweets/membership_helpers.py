from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import Engine, text
from sqlmodel import Session

from app.tweets.classes import list_post_sql, source_post_sql
from app.tweets.membership import MembershipSnapshot, save_snapshot

_BASE = datetime(2030, 1, 1, tzinfo=UTC)
_SNAPSHOT_COUNTER = [0]


def set_members(engine: Engine, handles: list[str] | None, list_id: int = 1) -> None:
    # None records a source without membership; each call is newer than the previous one.
    _SNAPSHOT_COUNTER[0] += 1
    fetched_at = _BASE + timedelta(seconds=_SNAPSHOT_COUNTER[0])
    save_snapshot(
        engine,
        MembershipSnapshot(
            fetched_at=fetched_at,
            source="fake",
            list_id=list_id,
            handles=None if handles is None else frozenset(h.lower() for h in handles),
        ),
    )


PostClass = Literal["list", "quoted", "context"]


def post_classes(session: Session, x_ids: list[int]) -> dict[int, PostClass]:
    if not x_ids:
        return {}
    rows = session.execute(
        text(
            f"SELECT t.x_id, CASE WHEN {list_post_sql('t')} THEN 'list'"
            f" WHEN {source_post_sql('t')} THEN 'quoted' ELSE 'context' END"
            " FROM tweet t WHERE t.x_id = ANY(:ids)"
        ),
        {"ids": list(x_ids)},
    )
    return {row[0]: row[1] for row in rows}
