from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlmodel import Session

from app.fpl.models import Gameweek, Season
from app.tweets.models import Tweet
from app.tweets.store import (
    PollRecord,
    last_seen_id,
    latest_poll,
    latest_success_by_source,
    store_posts,
    upcoming_deadlines,
    write_poll,
)
from tests.tweets.fakes import post

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def test_stored_post_fields(db):
    fetched_post = post(
        x_id=42,
        author_handle="synthetic_leaker",
        text="synthetic text",
        created_at=NOW,
        is_repost=True,
        is_reply=False,
        raw={"id": 42, "text": "synthetic text"},
    )
    with Session(db) as session, session.begin():
        new_count = store_posts(
            session, [fetched_post], "twitterapi_io", NOW + timedelta(seconds=5)
        )
    assert new_count == 1
    with Session(db) as session:
        row = session.get(Tweet, 42)
    assert row.x_id == 42
    assert row.author_handle == "synthetic_leaker"
    assert row.text == "synthetic text"
    assert row.created_at == NOW
    assert row.first_fetched_at == NOW + timedelta(seconds=5)
    assert row.source == "twitterapi_io"
    assert row.is_repost is True
    assert row.is_reply is False
    assert row.raw == {"id": 42, "text": "synthetic text"}


def test_same_post_stored_once_first_fetch_kept(db):
    first = post(x_id=43, created_at=NOW)
    with Session(db) as session, session.begin():
        first_count = store_posts(session, [first], "twscrape", NOW)
    assert first_count == 1

    again = post(x_id=43, created_at=NOW, author_handle="different_handle")
    with Session(db) as session, session.begin():
        second_count = store_posts(session, [again], "twitterapi_io", NOW + timedelta(minutes=1))
    assert second_count == 0

    with Session(db) as session:
        row = session.get(Tweet, 43)
    assert row.first_fetched_at == NOW
    assert row.source == "twscrape"


def test_store_posts_empty_list_stores_nothing(db):
    with Session(db) as session, session.begin():
        assert store_posts(session, [], "twscrape", NOW) == 0


def test_last_seen_id(db):
    with Session(db) as session:
        assert last_seen_id(session) is None
    with Session(db) as session, session.begin():
        store_posts(session, [post(x_id=10), post(x_id=20)], "twscrape", NOW)
    with Session(db) as session:
        assert last_seen_id(session) == 20


def _poll(
    source: str, started_at: datetime, outcome: str = "succeeded", new_posts: int = 1
) -> PollRecord:
    return PollRecord(
        source=source,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        outcome=outcome,
        new_posts=new_posts,
    )


def test_write_and_latest_poll(db):
    write_poll(db, _poll("twscrape", NOW))
    write_poll(db, _poll("twscrape", NOW + timedelta(seconds=20)))
    latest = latest_poll(db, "twscrape")
    assert latest.started_at == NOW + timedelta(seconds=20)
    assert latest_poll(db, "x_api") is None


def test_latest_success_per_source(db):
    write_poll(db, _poll("twscrape", NOW, outcome="failed", new_posts=0))
    write_poll(db, _poll("twscrape", NOW + timedelta(seconds=20)))
    write_poll(db, _poll("twitterapi_io", NOW + timedelta(seconds=10)))
    write_poll(
        db, _poll("twitterapi_io", NOW + timedelta(seconds=30), outcome="rate_limited", new_posts=0)
    )

    query_count = 0

    def _count(*_args, **_kwargs):
        nonlocal query_count
        query_count += 1

    event.listen(db, "before_cursor_execute", _count)
    try:
        result = latest_success_by_source(db)
    finally:
        event.remove(db, "before_cursor_execute", _count)

    assert query_count == 1
    assert result["twscrape"].started_at == NOW + timedelta(seconds=20)
    assert result["twitterapi_io"].started_at == NOW + timedelta(seconds=10)


def test_upcoming_deadlines(db):
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="Gameweek 6",
                deadline_at=NOW + timedelta(days=2),
                finished=False,
                data_checked=False,
            )
        )
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=5,
                name="Gameweek 5",
                deadline_at=NOW - timedelta(days=5),
                finished=True,
                data_checked=True,
            )
        )
    with Session(db) as session:
        deadlines = upcoming_deadlines(session, NOW)
    assert deadlines == [NOW + timedelta(days=2)]
