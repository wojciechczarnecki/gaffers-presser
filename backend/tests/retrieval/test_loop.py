import threading
import time
from datetime import timedelta

from sqlmodel import Session, select

from app.retrieval.loop import IndexingLoop, start_indexer
from app.retrieval.models import PostEmbedding
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import NOW, FastClock, add_tweet, runtime


def _rows(engine) -> list[PostEmbedding]:
    with Session(engine) as session:
        return list(session.exec(select(PostEmbedding).order_by(PostEmbedding.tweet_x_id)))


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_new_post_embedded_without_restart(db):
    stop = threading.Event()
    thread = start_indexer(db, runtime(FakeEmbedder()), stop, FastClock(stop))
    try:
        time.sleep(0.1)
        add_tweet(db, 1, "Saka is fit again", first_fetched_at=NOW)
        assert _wait_for(lambda: any(r.status == "embedded" for r in _rows(db)))
    finally:
        stop.set()
        thread.join(timeout=2)
    (row,) = _rows(db)
    assert row.latency_seconds is not None


def test_failure_does_not_stop_the_loop(db):
    stop = threading.Event()
    add_tweet(db, 1, created_at=NOW - timedelta(hours=2))
    add_tweet(db, 2, created_at=NOW - timedelta(hours=1))
    embedder = FakeEmbedder(responses=[RuntimeError("down")] * 3)
    thread = start_indexer(db, runtime(embedder), stop, FastClock(stop))
    try:
        assert _wait_for(lambda: {r.status for r in _rows(db)} == {"failed", "embedded"})
    finally:
        stop.set()
        thread.join(timeout=2)
    by_id = {r.tweet_x_id: r.status for r in _rows(db)}
    assert by_id == {1: "failed", 2: "embedded"}


def test_iteration_error_is_survived(db, caplog):
    stop = threading.Event()
    calls = {"n": 0}

    def flaky_factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cannot build")
        return FakeEmbedder()

    add_tweet(db, 1)
    clock = FastClock(stop)
    from app.retrieval.indexing import IndexingRuntime

    rt = IndexingRuntime(
        model="fake/embed",
        make_embedder=flaky_factory,
        prices=runtime(FakeEmbedder()).prices,
        tracing=None,
    )
    thread = start_indexer(db, rt, stop, clock)
    try:
        assert _wait_for(lambda: any(r.status == "embedded" for r in _rows(db)))
    finally:
        stop.set()
        thread.join(timeout=2)
    assert 30.0 in clock.sleeps
    assert "RuntimeError" in caplog.text


def test_stops_on_stop_event(db):
    stop = threading.Event()
    loop = IndexingLoop(db, runtime(FakeEmbedder()), FastClock(stop), stop)
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    time.sleep(0.1)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
