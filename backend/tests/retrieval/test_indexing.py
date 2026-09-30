import threading
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.retrieval.indexing import embed_post, index_missing
from app.retrieval.models import PostEmbedding
from app.retrieval.store import MAX_TOTAL_ATTEMPTS, next_unembedded
from app.retrieval.tracing import NULL_TRACER
from tests.retrieval.fakes import FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import MODEL, NOW, FixedClock, add_tweet, runtime


def _rows(engine) -> list[PostEmbedding]:
    with Session(engine) as session:
        return list(session.exec(select(PostEmbedding).order_by(PostEmbedding.tweet_x_id)))


def _next(engine, clock, model=MODEL):
    with Session(engine) as session:
        return next_unembedded(session, model, clock.now())


def _embed(engine, embedder, post, clock, tracer=NULL_TRACER, record_latency=True, model=MODEL):
    return embed_post(
        engine,
        runtime(embedder, model),
        embedder,
        post,
        tracer,
        clock,
        threading.Event(),
        record_latency,
    )


def test_embed_post_stores_model_dimensions_tokens_cost_latency(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1, "Saka is fit", first_fetched_at=NOW - timedelta(seconds=12))
    embedder = FakeEmbedder(default=[0.1, 0.2, 0.3, 0.4], tokens_per_text=5)

    outcome = _embed(db, embedder, _next(db, clock), clock)

    assert outcome is not None and outcome.status == "embedded"
    (row,) = _rows(db)
    assert (row.tweet_x_id, row.model, row.status) == (1, MODEL, "embedded")
    assert row.dimensions == 4
    assert list(row.embedding) == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert row.input_tokens == 5
    assert row.cost_usd == pytest.approx(5 * 0.02 / 1e6)
    assert row.latency_seconds == pytest.approx(12.0)
    assert row.attempts == 1
    assert row.updated_at == NOW
    assert embedder.calls == [["Saka is fit"]]


def test_latency_not_recorded_when_not_asked(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    _embed(db, FakeEmbedder(), _next(db, clock), clock, record_latency=False)
    assert _rows(db)[0].latency_seconds is None


def test_oldest_first(db):
    clock = FixedClock(NOW)
    add_tweet(db, 10, created_at=NOW - timedelta(hours=1))
    add_tweet(db, 11, created_at=NOW - timedelta(hours=3))
    add_tweet(db, 12, created_at=NOW - timedelta(hours=2))
    embedder = FakeEmbedder()
    order = []
    while (post := _next(db, clock)) is not None:
        order.append(post.x_id)
        _embed(db, embedder, post, clock)
    assert order == [11, 12, 10]


def test_three_attempts_then_failed_with_error_class(db, caplog):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    embedder = FakeEmbedder(responses=[RuntimeError("x"), RuntimeError("x"), RuntimeError("x")])

    outcome = _embed(db, embedder, _next(db, clock), clock)

    assert outcome is not None and outcome.status == "failed"
    assert len(embedder.calls) == 3
    assert clock.sleeps == [2.0, 4.0]
    (row,) = _rows(db)
    assert (row.status, row.error_class, row.attempts) == ("failed", "RuntimeError", 3)
    assert row.embedding is None
    assert "embedding of a post failed: RuntimeError" in caplog.text


def test_second_attempt_succeeds(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    embedder = FakeEmbedder(responses=[RuntimeError("x"), None])
    _embed(db, embedder, _next(db, clock), clock)
    (row,) = _rows(db)
    assert (row.status, row.attempts) == ("embedded", 2)
    assert clock.sleeps == [2.0]


def test_failed_post_not_retried_before_10_minutes(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    embedder = FakeEmbedder(responses=[RuntimeError("x")] * 3)
    _embed(db, embedder, _next(db, clock), clock)

    clock.current = NOW + timedelta(minutes=9)
    assert _next(db, clock) is None
    clock.current = NOW + timedelta(minutes=10)
    retried = _next(db, clock)
    assert retried is not None and retried.x_id == 1


def test_failed_post_does_not_block_the_next_one(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1, created_at=NOW - timedelta(hours=2))
    add_tweet(db, 2, created_at=NOW - timedelta(hours=1))
    _embed(db, FakeEmbedder(responses=[RuntimeError("x")] * 3), _next(db, clock), clock)
    following = _next(db, clock)
    assert following is not None and following.x_id == 2


def test_success_after_failure_replaces_the_failed_row(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    _embed(db, FakeEmbedder(responses=[RuntimeError("x")] * 3), _next(db, clock), clock)
    clock.current = NOW + timedelta(minutes=11)
    _embed(db, FakeEmbedder(), _next(db, clock), clock)
    (row,) = _rows(db)
    assert (row.status, row.error_class, row.attempts) == ("embedded", None, 4)
    assert row.embedding is not None


def test_other_model_rows_untouched(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    other = FakeEmbedder(model="other/embed", default=[9.0, 9.0, 9.0])
    _embed(db, other, _next(db, clock, "other/embed"), clock, model="other/embed")
    before = [(r.model, list(r.embedding)) for r in _rows(db)]

    _embed(db, FakeEmbedder(default=[1.0, 2.0, 3.0]), _next(db, clock), clock)

    rows = _rows(db)
    assert sorted(r.model for r in rows) == [MODEL, "other/embed"]
    assert [(r.model, list(r.embedding)) for r in rows if r.model == "other/embed"] == before


def test_embedding_call_traced_with_model_tokens_cost(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1, "Saka is fit")
    tracer = RecordingTracer()
    _embed(db, FakeEmbedder(tokens_per_text=7), _next(db, clock), clock, tracer=tracer)
    (record,) = tracer.embeddings
    assert record["model"] == MODEL
    assert record["texts"] == ["Saka is fit"]
    assert record["input_tokens"] == 7
    assert record["cost_usd"] == pytest.approx(7 * 0.02 / 1e6)


def test_stopped_returns_none_without_a_row(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    stop = threading.Event()
    stop.set()
    embedder = FakeEmbedder()
    outcome = embed_post(
        db, runtime(embedder), embedder, _next(db, clock), NULL_TRACER, clock, stop, True
    )
    assert outcome is None
    assert _rows(db) == []


def test_index_missing_counts_and_retries_failed_ones(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1, created_at=NOW - timedelta(hours=2))
    add_tweet(db, 2, created_at=NOW - timedelta(hours=1))
    embedder = FakeEmbedder(responses=[RuntimeError("x")] * 3, tokens_per_text=10)

    summary = index_missing(db, runtime(embedder), embedder, NULL_TRACER, clock, threading.Event())

    assert (summary.embedded, summary.failed) == (1, 1)
    assert summary.cost_usd == pytest.approx(10 * 0.02 / 1e6)

    again = index_missing(db, runtime(embedder), embedder, NULL_TRACER, clock, threading.Event())
    assert (again.embedded, again.failed) == (1, 0)
    third = index_missing(db, runtime(embedder), embedder, NULL_TRACER, clock, threading.Event())
    assert (third.embedded, third.failed, third.cost_usd) == (0, 0, None)


class RaisingTracer(RecordingTracer):
    def embedding(self, **kwargs) -> None:
        raise ConnectionError("tracing backend down")


def test_tracing_error_neither_retries_nor_fails_a_paid_embedding(db, caplog):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    embedder = FakeEmbedder()
    outcome = _embed(db, embedder, _next(db, clock), clock, tracer=RaisingTracer())
    assert outcome is not None and outcome.status == "embedded"
    assert len(embedder.calls) == 1
    (row,) = _rows(db)
    assert (row.status, row.attempts) == ("embedded", 1)
    assert "retrieval tracing failed: ConnectionError" in caplog.text


def test_failed_embedding_calls_are_traced_with_the_error_class(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1, "Saka is fit")
    tracer = RecordingTracer()
    embedder = FakeEmbedder(responses=[RuntimeError("x"), None])
    _embed(db, embedder, _next(db, clock), clock, tracer=tracer)
    failed, succeeded = tracer.embeddings
    assert failed["error_class"] == "RuntimeError"
    assert (failed["input_tokens"], failed["cost_usd"]) == (None, None)
    assert "error_class" not in succeeded
    assert succeeded["input_tokens"] == 5


def _fail_once(db, clock) -> None:
    _embed(db, FakeEmbedder(responses=[RuntimeError("x")] * 3), _next(db, clock), clock)


def test_fresh_post_goes_before_an_older_failed_one(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1, created_at=NOW - timedelta(hours=5))
    _fail_once(db, clock)
    add_tweet(db, 2, created_at=NOW - timedelta(minutes=1))
    clock.current = NOW + timedelta(minutes=30)
    fresh = _next(db, clock)
    assert fresh is not None and fresh.x_id == 2


def test_attempts_accumulate_and_retries_stop_at_the_cap(db):
    clock = FixedClock(NOW)
    add_tweet(db, 1)
    for passes in range(1, 6):
        _fail_once(db, clock)
        assert _rows(db)[0].attempts == 3 * passes
        clock.current += timedelta(minutes=10)
    assert _rows(db)[0].attempts == MAX_TOTAL_ATTEMPTS
    assert _next(db, clock) is None
    assert _rows(db)[0].status == "failed"
