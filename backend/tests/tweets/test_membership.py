from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from app.tweets.membership import (
    MembershipSnapshot,
    fetch_membership,
    latest_snapshot,
    refresh_membership,
    save_snapshot,
)
from app.tweets.sources.base import SourceRateLimitedError
from tests.tweets.fakes import FakeSource

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def test_fetch_lowercases_handles():
    source = FakeSource([], members=["Synthetic_Leaker_1", "OTHER"])
    snapshot = fetch_membership(source, 7, NOW)
    assert snapshot == MembershipSnapshot(
        fetched_at=NOW,
        source="fake",
        list_id=7,
        handles=frozenset({"synthetic_leaker_1", "other"}),
    )


def test_unsupported_gives_null_handles():
    snapshot = fetch_membership(FakeSource([]), 7, NOW)
    assert snapshot.handles is None
    assert snapshot.source == "fake"


def test_errors_propagate_from_fetch():
    source = FakeSource([], members=SourceRateLimitedError("limited"))
    with pytest.raises(SourceRateLimitedError):
        fetch_membership(source, 7, NOW)


def test_save_and_latest_snapshot(db):
    with Session(db) as session:
        assert latest_snapshot(session) is None
    save_snapshot(db, MembershipSnapshot(NOW, "twscrape", 7, frozenset({"a", "b"})))
    later = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
    save_snapshot(db, MembershipSnapshot(later, "twscrape", 7, frozenset({"c"})))
    with Session(db) as session:
        snapshot = latest_snapshot(session)
    assert snapshot == MembershipSnapshot(later, "twscrape", 7, frozenset({"c"}))


def test_null_snapshot_round_trips(db):
    save_snapshot(db, MembershipSnapshot(NOW, "x_api", 7, None))
    with Session(db) as session:
        assert latest_snapshot(session).handles is None


def test_refresh_fetches_and_saves(db):
    snapshot = refresh_membership(db, FakeSource([], members=["A"]), 7, NOW)
    with Session(db) as session:
        assert latest_snapshot(session) == snapshot
