from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.tweets.ingest import poll_once
from app.tweets.models import Tweet, TweetPoll
from app.tweets.sources.base import SourcePayloadError, SourceRateLimitedError
from tests.tweets.fakes import FakeSource, post

START = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _clock(times: list[datetime]):
    it = iter(times)

    def now_fn() -> datetime:
        return next(it)

    return now_fn


def _poll_rows(db) -> list[TweetPoll]:
    with Session(db) as session:
        return list(session.exec(select(TweetPoll)).all())


def _assert_single_row(db, **expected) -> None:
    rows = _poll_rows(db)
    assert len(rows) == 1
    row = rows[0]
    for field, value in expected.items():
        assert getattr(row, field) == value, field


def test_every_poll_leaves_a_record_success(db):
    source = FakeSource(
        pages=[[post(x_id=1, author_handle="synthetic_leaker_1", created_at=START)]]
    )
    now_fn = _clock([START, START + timedelta(seconds=1), START + timedelta(seconds=2)])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.source == "fake"
    assert record.outcome == "succeeded"
    assert record.new_posts == 1
    assert record.started_at == START
    assert record.finished_at == START + timedelta(seconds=2)
    _assert_single_row(
        db,
        source="fake",
        started_at=START,
        finished_at=START + timedelta(seconds=2),
        outcome="succeeded",
        new_posts=1,
        error_class=None,
        retry_after_seconds=None,
    )
    with Session(db) as session:
        tweet = session.get(Tweet, 1)
    assert tweet.first_fetched_at == START + timedelta(seconds=1)
    assert tweet.source == "fake"
    assert tweet.author_handle == "synthetic_leaker_1"


def test_every_poll_leaves_a_record_rate_limited(db):
    source = FakeSource(pages=[SourceRateLimitedError("rate limited", retry_after=45)])
    now_fn = _clock([START, START + timedelta(seconds=1)])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.outcome == "rate_limited"
    assert record.retry_after_seconds == 45
    assert record.new_posts == 0
    _assert_single_row(
        db,
        source="fake",
        started_at=START,
        finished_at=START + timedelta(seconds=1),
        outcome="rate_limited",
        new_posts=0,
        error_class="SourceRateLimitedError",
        retry_after_seconds=45,
    )


def test_every_poll_leaves_a_record_failed(db):
    source = FakeSource(pages=[SourcePayloadError("bad payload")])
    now_fn = _clock([START, START + timedelta(seconds=1)])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.outcome == "failed"
    assert record.error_class == "SourcePayloadError"
    assert record.new_posts == 0
    _assert_single_row(
        db,
        source="fake",
        started_at=START,
        finished_at=START + timedelta(seconds=1),
        outcome="failed",
        new_posts=0,
        error_class="SourcePayloadError",
        retry_after_seconds=None,
    )


def test_second_poll_pages_down_to_the_stored_max_id(db):
    first = FakeSource(pages=[[post(x_id=5, created_at=START)]])
    poll_once(db, first, list_id=123, now_fn=_clock([START, START, START]))

    # Without since_id the poll would stop after page 1; with it, paging reaches post 5.
    second = FakeSource(
        pages=[
            [post(x_id=7, created_at=START), post(x_id=6, created_at=START)],
            [post(x_id=5, created_at=START), post(x_id=4, created_at=START)],
            [post(x_id=3, created_at=START)],
        ]
    )
    later = START + timedelta(seconds=20)
    record = poll_once(db, second, list_id=123, now_fn=_clock([later, later, later]))

    assert record.outcome == "succeeded"
    assert second.pull_count == 2
    # Post 4 became visible after post 5 was stored; it is kept, post 5 is not duplicated.
    assert record.new_posts == 3
    with Session(db) as session:
        stored = sorted(session.exec(select(Tweet.x_id)).all())
    assert stored == [4, 5, 6, 7]


def test_failed_poll_does_not_store_posts_on_database_error(db, monkeypatch):
    from app.tweets import ingest as ingest_module

    def _broken_store_posts(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(ingest_module, "store_posts", _broken_store_posts)
    source = FakeSource(pages=[[post(x_id=9, created_at=START)]])
    now_fn = _clock([START, START, START])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.outcome == "failed"
    assert record.error_class == "RuntimeError"
    with Session(db) as session:
        assert session.get(Tweet, 9) is None


def test_failed_poll_record_and_logs_carry_no_secret(db, caplog):
    class SecretLeakingSource(FakeSource):
        def pages(self, list_id):
            raise SourcePayloadError("token=sentinel-secret-xyz leaked")

    source = SecretLeakingSource(pages=[])
    now_fn = _clock([START, START])
    with caplog.at_level("INFO"):
        record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.outcome == "failed"
    assert record.error_class == "SourcePayloadError"
    assert "sentinel-secret" not in caplog.text

    rows = _poll_rows(db)
    assert len(rows) == 1
    assert rows[0].error_class == "SourcePayloadError"
    assert "sentinel-secret" not in repr(rows[0].model_dump())


def test_catch_up_fills_a_gap_longer_than_a_regular_poll(db):
    with Session(db) as session, session.begin():
        session.add(_stored(1))
    pages = [[post(x_id=20 - i, created_at=START - timedelta(hours=i))] for i in range(19)]
    source = FakeSource(pages=pages, max_pages=50)
    record = poll_once(
        db, source, list_id=123, now_fn=lambda: START, floor=START - timedelta(days=7)
    )
    assert record.new_posts == 19
    assert source.pull_count == 19


def test_rate_limit_during_catch_up_stores_nothing(db):
    with Session(db) as session, session.begin():
        session.add(_stored(1))
    source = FakeSource(
        pages=[[post(x_id=9, created_at=START)], SourceRateLimitedError("limited", 60.0)],
        max_pages=50,
    )
    record = poll_once(
        db, source, list_id=123, now_fn=lambda: START, floor=START - timedelta(days=7)
    )
    assert record.outcome == "rate_limited"
    with Session(db) as session:
        assert [t.x_id for t in session.exec(select(Tweet)).all()] == [1]


def _stored(x_id: int) -> Tweet:
    return Tweet(
        x_id=x_id,
        author_handle="synthetic_leaker",
        text="synthetic text",
        created_at=START - timedelta(days=1),
        first_fetched_at=START - timedelta(days=1),
        source="fake",
        is_repost=False,
        is_reply=False,
        raw={"id": x_id},
    )
