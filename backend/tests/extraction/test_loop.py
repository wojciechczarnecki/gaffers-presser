import threading
import time
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.extraction.loop import ExtractionLoop
from app.extraction.models import Extraction
from app.extraction.schemas import ExtractionOutput
from app.extraction.service import ExtractionRuntime
from app.llm.chat import ChatModelSpec
from app.tweets.models import Tweet
from tests.extraction.fakes import FakeChatModel
from tests.tweets.membership_helpers import set_members

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _tweet_row(x_id: int, created_at: datetime = NOW, author: str = "reporter") -> Tweet:
    return Tweet(
        x_id=x_id,
        author_handle=author,
        text="Haaland starts today.",
        created_at=created_at,
        first_fetched_at=created_at,
        source="list",
        is_repost=False,
        is_reply=False,
        raw={},
    )


def _runtime(*responses) -> ExtractionRuntime:
    fake = FakeChatModel(responses=list(responses))
    spec = ChatModelSpec(provider="fake", model="fake-model", chat_model=fake)
    return ExtractionRuntime(
        provider="fake",
        model="fake-model",
        make_spec=lambda: spec,
        tracing=None,
        prices={},
        aliases=([], []),
    )


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)


def test_new_post_picked_within_5_s(db):
    stop_event = threading.Event()

    class ScriptedClock:
        def __init__(self) -> None:
            self._now = NOW
            self._inserted = False

        def now(self) -> datetime:
            return self._now

        def sleep(self, seconds: float) -> None:
            self._now += timedelta(seconds=seconds)
            if not self._inserted:
                self._inserted = True
                with Session(db) as session:
                    session.add(_tweet_row(x_id=1))
                    session.commit()

    clock = ScriptedClock()
    runtime = _runtime(ExtractionOutput(events=[]))
    loop = ExtractionLoop(db, runtime, clock, stop_event)

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()

    rows: list[Extraction] = []

    def _fetch() -> bool:
        nonlocal rows
        with Session(db) as session:
            rows = session.exec(select(Extraction)).all()
        return len(rows) >= 1

    _wait_for(_fetch)
    stop_event.set()
    thread.join(timeout=2)

    assert len(rows) == 1
    assert (rows[0].started_at - NOW).total_seconds() <= 5


def test_oldest_first(db):
    stop_event = threading.Event()
    with Session(db) as session:
        session.add(_tweet_row(x_id=2, created_at=NOW + timedelta(minutes=1)))
        session.add(_tweet_row(x_id=1, created_at=NOW))
        session.commit()

    class CountingClock:
        def __init__(self) -> None:
            self._now = NOW

        def now(self) -> datetime:
            return self._now

        def sleep(self, seconds: float) -> None:
            self._now += timedelta(seconds=seconds)

    runtime = _runtime(ExtractionOutput(events=[]), ExtractionOutput(events=[]))
    loop = ExtractionLoop(db, runtime, CountingClock(), stop_event)

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()

    rows: list[Extraction] = []

    def _fetch() -> bool:
        nonlocal rows
        with Session(db) as session:
            rows = session.exec(select(Extraction).order_by(Extraction.id)).all()
        return len(rows) >= 2

    _wait_for(_fetch)
    stop_event.set()
    thread.join(timeout=2)

    assert [row.tweet_x_id for row in rows] == [1, 2]


def test_failure_does_not_stop_the_loop(db):
    stop_event = threading.Event()
    with Session(db) as session:
        session.add(_tweet_row(x_id=1, created_at=NOW))
        session.add(_tweet_row(x_id=2, created_at=NOW + timedelta(minutes=1)))
        session.commit()

    class NoSleepClock:
        def __init__(self) -> None:
            self._now = NOW

        def now(self) -> datetime:
            return self._now

        def sleep(self, seconds: float) -> None:
            self._now += timedelta(seconds=seconds)

    runtime = _runtime(
        RuntimeError("a"), RuntimeError("b"), RuntimeError("c"), ExtractionOutput(events=[])
    )
    loop = ExtractionLoop(db, runtime, NoSleepClock(), stop_event)

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()

    rows: list[Extraction] = []

    def _fetch() -> bool:
        nonlocal rows
        with Session(db) as session:
            rows = session.exec(select(Extraction).order_by(Extraction.id)).all()
        return len(rows) >= 2

    _wait_for(_fetch)
    stop_event.set()
    thread.join(timeout=2)

    assert len(rows) == 2
    by_x_id = {row.tweet_x_id: row.status for row in rows}
    assert by_x_id[1] == "failed"
    assert by_x_id[2] == "extracted"


def test_db_error_survives_iteration(db, monkeypatch, caplog):
    stop_event = threading.Event()
    with Session(db) as session:
        session.add(_tweet_row(x_id=1, created_at=NOW))
        session.commit()

    from app.extraction import loop as loop_module

    original_next_pending = loop_module.next_pending
    calls = {"n": 0}

    def flaky_next_pending(session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return original_next_pending(session)

    monkeypatch.setattr(loop_module, "next_pending", flaky_next_pending)

    class NoSleepClock:
        def __init__(self) -> None:
            self._now = NOW
            self.sleeps: list[float] = []

        def now(self) -> datetime:
            return self._now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self._now += timedelta(seconds=seconds)

    clock = NoSleepClock()
    runtime = _runtime(ExtractionOutput(events=[]))
    loop = ExtractionLoop(db, runtime, clock, stop_event)

    rows: list[Extraction] = []

    def _fetch() -> bool:
        nonlocal rows
        with Session(db) as session:
            rows = session.exec(select(Extraction)).all()
        return len(rows) >= 1

    with caplog.at_level("ERROR"):
        thread = threading.Thread(target=loop.run, daemon=True)
        thread.start()
        _wait_for(_fetch)
        stop_event.set()
        thread.join(timeout=2)

    assert len(rows) == 1
    assert 30.0 in clock.sleeps
    assert "RuntimeError" in caplog.text


def test_stops_on_stop_event(db):
    stop_event = threading.Event()

    class WaitingClock:
        def now(self) -> datetime:
            return NOW

        def sleep(self, seconds: float) -> None:
            stop_event.wait(min(seconds, 0.05))

    runtime = _runtime()
    loop = ExtractionLoop(db, runtime, WaitingClock(), stop_event)

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    time.sleep(0.1)
    stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive()


class _AdvancingClock:
    def __init__(self) -> None:
        self._now = NOW
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)


def _run_until_rows(db, loop: ExtractionLoop, stop_event: threading.Event, count: int):
    rows: list[Extraction] = []

    def _fetch() -> bool:
        nonlocal rows
        with Session(db) as session:
            rows = session.exec(select(Extraction).order_by(Extraction.id)).all()
        return len(rows) >= count

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    _wait_for(_fetch)
    stop_event.set()
    thread.join(timeout=2)
    return rows


def test_error_while_saving_stores_failed_and_moves_on(db, monkeypatch):
    stop_event = threading.Event()
    with Session(db) as session:
        session.add(_tweet_row(x_id=1, created_at=NOW))
        session.add(_tweet_row(x_id=2, created_at=NOW + timedelta(minutes=1)))
        session.commit()

    import app.extraction.service as service

    real_save = service.save_extraction

    def save_failing_for_first_post(session, record, events):
        if record.tweet_x_id == 1 and record.status == "extracted":
            raise RuntimeError("database write failed")
        return real_save(session, record, events)

    monkeypatch.setattr(service, "save_extraction", save_failing_for_first_post)
    runtime = _runtime(ExtractionOutput(events=[]), ExtractionOutput(events=[]))
    clock = _AdvancingClock()
    loop = ExtractionLoop(db, runtime, clock, stop_event)

    rows = _run_until_rows(db, loop, stop_event, 2)

    assert [(row.tweet_x_id, row.status, row.error_class) for row in rows] == [
        (1, "failed", "RuntimeError"),
        (2, "extracted", None),
    ]
    assert 30.0 not in clock.sleeps


def test_tracing_setup_error_extracts_untraced(db, monkeypatch, caplog):
    stop_event = threading.Event()
    with Session(db) as session:
        session.add(_tweet_row(x_id=1, created_at=NOW))
        session.commit()

    def broken_handler(tracing):
        raise ValueError("langfuse client cannot start with secret sk-lf-sentinel")

    monkeypatch.setattr("app.extraction.loop.make_handler", broken_handler)
    runtime = _runtime(ExtractionOutput(events=[]))
    loop = ExtractionLoop(db, runtime, _AdvancingClock(), stop_event)

    with caplog.at_level("ERROR"):
        rows = _run_until_rows(db, loop, stop_event, 1)

    assert [(row.tweet_x_id, row.status) for row in rows] == [(1, "extracted")]
    assert "ValueError" in caplog.text
    assert "sk-lf-sentinel" not in caplog.text


def test_loop_skips_context_posts(db):
    with Session(db) as session:
        session.add(_tweet_row(1, author="outsider"))
        session.add(_tweet_row(2, NOW + timedelta(seconds=1)))
        session.commit()
    set_members(db, ["reporter"])
    stop_event = threading.Event()

    class Clock:
        def now(self) -> datetime:
            return NOW

        def sleep(self, seconds: float) -> None:
            time.sleep(0.01)

    loop = ExtractionLoop(db, _runtime(ExtractionOutput(events=[])), Clock(), stop_event)
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()

    def extracted() -> list[int]:
        with Session(db) as session:
            return sorted(session.exec(select(Extraction.tweet_x_id)).all())

    _wait_for(lambda: extracted() == [2])
    time.sleep(0.2)
    stop_event.set()
    thread.join(timeout=5)

    assert extracted() == [2]
