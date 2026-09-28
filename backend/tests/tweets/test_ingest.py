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


def test_every_poll_leaves_a_record_success(db):
    source = FakeSource(pages=[[post(x_id=1, created_at=START)]])
    now_fn = _clock([START, START + timedelta(seconds=1), START + timedelta(seconds=2)])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.source == "fake"
    assert record.outcome == "succeeded"
    assert record.new_posts == 1
    assert record.started_at == START
    assert record.finished_at == START + timedelta(seconds=2)
    with Session(db) as session:
        assert session.get(Tweet, 1) is not None


def test_every_poll_leaves_a_record_rate_limited(db):
    source = FakeSource(pages=[SourceRateLimitedError("rate limited", retry_after=45)])
    now_fn = _clock([START, START + timedelta(seconds=1)])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.outcome == "rate_limited"
    assert record.retry_after_seconds == 45
    assert record.new_posts == 0


def test_every_poll_leaves_a_record_failed(db):
    source = FakeSource(pages=[SourcePayloadError("bad payload")])
    now_fn = _clock([START, START + timedelta(seconds=1)])
    record = poll_once(db, source, list_id=123, now_fn=now_fn)
    assert record.outcome == "failed"
    assert record.error_class == "SourcePayloadError"
    assert record.new_posts == 0


class RecordingSource(FakeSource):
    def pages(self, list_id):
        self.seen_list_ids: list[int] = getattr(self, "seen_list_ids", [])
        self.seen_list_ids.append(list_id)
        return super().pages(list_id)


def test_second_poll_passes_stored_max_id_as_since_id(db):
    first = FakeSource(pages=[[post(x_id=5, created_at=START)]])
    now_fn = _clock([START, START, START])
    poll_once(db, first, list_id=123, now_fn=now_fn)

    second = RecordingSource(
        pages=[[post(x_id=5, created_at=START), post(x_id=6, created_at=START)]]
    )
    later = START + timedelta(seconds=20)
    now_fn = _clock([later, later, later])
    record = poll_once(db, second, list_id=123, now_fn=now_fn)
    assert record.outcome == "succeeded"
    assert record.new_posts == 1
    assert second.seen_list_ids == [123]


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
    assert "sentinel-secret-xyz" not in caplog.text

    with Session(db) as session:
        rows = session.exec(select(TweetPoll)).all()
    for row in rows:
        assert row.error_class is None or "sentinel-secret" not in row.error_class
