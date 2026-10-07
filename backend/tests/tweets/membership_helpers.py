from datetime import UTC, datetime

from sqlalchemy import Engine

from app.tweets.membership import MembershipSnapshot, save_snapshot

_SNAPSHOT_COUNTER = [0]


def set_members(engine: Engine, handles: list[str] | None, list_id: int = 1) -> None:
    """Record a newer membership snapshot; None records a source without membership."""
    _SNAPSHOT_COUNTER[0] += 1
    fetched_at = datetime(2030, 1, 1, tzinfo=UTC).replace(second=_SNAPSHOT_COUNTER[0] % 60)
    save_snapshot(
        engine,
        MembershipSnapshot(
            fetched_at=fetched_at,
            source="fake",
            list_id=list_id,
            handles=None if handles is None else frozenset(h.lower() for h in handles),
        ),
    )
