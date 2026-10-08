from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from app.tweets.membership import (
    MAX_REMOVED_MEMBERS,
    MembershipShrunkError,
    MembershipSnapshot,
    check_shrink,
    fetch_membership,
    latest_snapshot,
    save_snapshot,
    store_membership,
)
from app.tweets.sources.base import SourcePayloadError, SourceRateLimitedError
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


def test_empty_member_list_is_a_payload_error():
    with pytest.raises(SourcePayloadError, match="fake: empty member list"):
        fetch_membership(FakeSource([], members=[]), 7, NOW)


def test_latest_snapshot_tie_goes_to_the_later_row(db):
    save_snapshot(db, MembershipSnapshot(NOW, "twscrape", 7, frozenset({"first"})))
    save_snapshot(db, MembershipSnapshot(NOW, "twscrape", 7, frozenset({"second"})))
    with Session(db) as session:
        assert latest_snapshot(session).handles == frozenset({"second"})


def _snapshot(handles, list_id=7):
    return MembershipSnapshot(NOW, "twscrape", list_id, handles)


_FULL = frozenset(f"m{i}" for i in range(10))


def test_check_shrink_accepts_a_few_removals_and_additions():
    kept = frozenset(sorted(_FULL)[MAX_REMOVED_MEMBERS:]) | {"new"}
    check_shrink(_snapshot(_FULL), _snapshot(kept))
    check_shrink(None, _snapshot(frozenset({"a"})))
    check_shrink(_snapshot(None), _snapshot(frozenset({"a"})))
    check_shrink(_snapshot(_FULL), _snapshot(None))
    check_shrink(_snapshot(_FULL, list_id=8), _snapshot(frozenset({"a"})))


def test_check_shrink_refuses_a_sharp_drop():
    truncated = frozenset(sorted(_FULL)[MAX_REMOVED_MEMBERS + 1 :])
    with pytest.raises(MembershipShrunkError) as exc_info:
        check_shrink(_snapshot(_FULL), _snapshot(truncated))
    assert (exc_info.value.previous, exc_info.value.current) == (10, len(truncated))


def test_store_membership_keeps_the_previous_snapshot_on_a_sharp_drop(db):
    store_membership(db, _snapshot(_FULL))
    with pytest.raises(MembershipShrunkError):
        store_membership(db, _snapshot(frozenset({"m0"})))
    with Session(db) as session:
        assert latest_snapshot(session).handles == _FULL
    store_membership(db, _snapshot(frozenset({"m0"})), force=True)
    with Session(db) as session:
        assert latest_snapshot(session).handles == frozenset({"m0"})


def test_a_member_fetch_cut_after_page_one_does_not_replace_the_snapshot(db):
    from app.tweets.sources.twscrape_source import TwscrapeSource
    from tests.tweets.sources.test_twscrape_source import FakeApi, _members_page

    page_1 = ["p1_a", "p1_b"]
    page_2 = [f"p2_{i}" for i in range(MAX_REMOVED_MEMBERS + 1)]

    def fetch(pages):
        api = FakeApi([], member_pages=pages)
        source = TwscrapeSource("dedicated", "auth_token=a; ct0=b", ":unused:", api=api)
        try:
            return fetch_membership(source, 7, NOW)
        finally:
            source.close()

    store_membership(db, fetch([_members_page(page_1), _members_page(page_2)]))
    with pytest.raises(MembershipShrunkError):
        store_membership(db, fetch([_members_page(page_1)]))
    with Session(db) as session:
        assert latest_snapshot(session).handles == frozenset(page_1 + page_2)
