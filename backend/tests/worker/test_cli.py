import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import typer
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.db.engine import make_engine
from app.db.locks import SCHEDULE_LOCK_KEY
from app.extraction.schemas import ExtractionOutput
from app.extraction.service import ExtractionRuntime
from app.fpl.models import Gameweek, Season
from app.llm.chat import ChatModelSpec
from app.tweets.models import TweetPoll
from app.worker.cli import AlertsSetup, TweetIngest, WorkerDeps, app
from app.worker.jobs import Shutdown
from app.worker.models import JobRun
from app.worker.schedule import Job
from tests.conftest import BACKEND_DIR, held_advisory_lock
from tests.extraction.fakes import FakeChatModel
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load
from tests.retrieval.fakes import AlwaysFailingEmbedder, FakeEmbedder
from tests.retrieval.helpers import runtime as indexing_runtime
from tests.tweets.fakes import FakeSource
from tests.worker.sim import FakeClock

EXTRACTION_VARIABLE = re.compile(
    r"(LLM_.*|LANGFUSE_.*|.*_API_KEY|USD_PLN_RATE|EMBEDDING_MODEL|DELIVERY_.*|ALERT.*)"
)
NOW = datetime(2026, 9, 26, tzinfo=UTC)
D6 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_extraction_variables(monkeypatch):
    for name in list(os.environ):
        if EXTRACTION_VARIABLE.fullmatch(name):
            monkeypatch.delenv(name)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        pass


class RealClock:
    """A clock with a fixed `now` and a real, interruptible `sleep`."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ReleasingClock:
    def __init__(self, now, release_after, end_after, release_fn) -> None:
        self._now = now
        self._elapsed = timedelta(0)
        self._release_after = release_after
        self._end_after = end_after
        self._release_fn = release_fn
        self._released = False

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._elapsed += timedelta(seconds=seconds)
        self._now += timedelta(seconds=seconds)
        if not self._released and self._elapsed >= self._release_after:
            self._released = True
            self._release_fn()
        if self._elapsed >= self._end_after:
            raise Shutdown


@pytest.fixture
def cli(db):
    def invoke(
        *args,
        client=None,
        league_ids_raw="1",
        clock=None,
        tweet_ingest=None,
        extraction=None,
        indexing=None,
        delivery_channel=None,
        alerts=None,
        alerts_disabled_reason=None,
    ):
        deps = WorkerDeps(
            engine=db,
            client=client or FakeFpl({}).client(sleep=lambda _: None),
            league_ids_raw=league_ids_raw,
            clock=clock or FixedClock(NOW),
            tweet_ingest=tweet_ingest,
            extraction=extraction,
            indexing=indexing,
            delivery_channel=delivery_channel,
            alerts=alerts,
            alerts_disabled_reason=alerts_disabled_reason,
        )
        return CliRunner().invoke(app, list(args), obj=deps)

    return invoke


def _extraction_runtime_and_fake(
    *responses, tracing=None
) -> tuple[ExtractionRuntime, FakeChatModel]:
    fake = FakeChatModel(responses=list(responses))
    spec = ChatModelSpec(provider="fake", model="fake-model", chat_model=fake)
    runtime = ExtractionRuntime(
        provider="fake",
        model="fake-model",
        make_spec=lambda: spec,
        tracing=tracing,
        prices={},
        aliases=([], []),
    )
    return runtime, fake


def _extraction_runtime(*responses, tracing=None) -> ExtractionRuntime:
    return _extraction_runtime_and_fake(*responses, tracing=tracing)[0]


def _seed_tweet(db, x_id: int = 1, first_fetched_at: datetime = NOW) -> None:
    from app.tweets.models import Tweet

    with Session(db) as session:
        session.add(
            Tweet(
                x_id=x_id,
                author_handle="reporter",
                text="Haaland starts.",
                created_at=first_fetched_at,
                first_fetched_at=first_fetched_at,
                source="fake",
                is_repost=False,
                is_reply=False,
                raw={},
            )
        )
        session.commit()


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _sigterm_when(predicate) -> threading.Thread:
    def target() -> None:
        _wait_until(predicate)
        os.kill(os.getpid(), signal.SIGTERM)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _extractions(db):
    from app.extraction.models import Extraction

    with Session(db) as session:
        return session.exec(select(Extraction)).all()


def test_status_on_empty_database(cli):
    result = cli("status")
    assert result.exit_code == 0
    for job in Job:
        assert f"{job.value}: never" in result.stdout
    assert "reference_sync gameweek=-: due now" in result.stdout


def test_status_shows_latest_runs_and_next_actions(cli, db):
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=D6,
                finished=False,
                data_checked=False,
            )
        )
        session.add(
            JobRun(
                job="reference_sync",
                season="2026/27",
                gameweek_fpl_id=None,
                started_at=D6 - timedelta(hours=1),
                finished_at=D6 - timedelta(hours=1) + timedelta(seconds=5),
                outcome="succeeded",
            )
        )

    result = cli("status", clock=FixedClock(D6 - timedelta(hours=1)))
    assert result.exit_code == 0
    assert "reference_sync: 2026-10-10T09:00:00Z gameweek=- outcome=succeeded" in result.stdout
    lines = result.stdout.splitlines()
    snapshot_lines = [line.strip() for line in lines if "deadline_snapshot gameweek=6:" in line]
    assert snapshot_lines == [
        "deadline_snapshot gameweek=6: 2026-10-10T09:30:00Z",
        "deadline_snapshot gameweek=6: 2026-10-10T09:55:00Z",
    ]


def test_second_worker_waits_for_schedule_lock(cli, db):
    released_at = NOW + timedelta(minutes=10)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    with held_advisory_lock(db, SCHEDULE_LOCK_KEY) as release:
        clock = ReleasingClock(
            NOW,
            release_after=timedelta(minutes=10),
            end_after=timedelta(hours=1),
            release_fn=release,
        )
        result = cli("run", client=fake.client(sleep=lambda _: None), clock=clock)

    assert result.exit_code == 0
    with Session(db) as session:
        rows = session.exec(select(JobRun)).all()
    assert rows
    assert all(r.started_at >= released_at for r in rows)


def test_sigterm_while_waiting_for_schedule_lock_exits_0(cli, db):
    with held_advisory_lock(db, SCHEDULE_LOCK_KEY):
        timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            start = time.monotonic()
            result = cli("run", clock=RealClock(NOW))
            elapsed = time.monotonic() - start
        finally:
            timer.cancel()

    assert result.exit_code == 0
    assert elapsed < 10
    with Session(db) as session:
        assert session.exec(select(JobRun)).all() == []


def test_unreachable_database_exits_1_with_error_class(caplog):
    engine = make_engine("postgresql+psycopg://nobody:secret@127.0.0.1:1/none")
    previous = signal.getsignal(signal.SIGTERM)
    deps = WorkerDeps(
        engine=engine,
        client=FakeFpl({}).client(sleep=lambda _: None),
        league_ids_raw="1",
        clock=FixedClock(NOW),
    )
    with caplog.at_level(logging.INFO):
        result = CliRunner().invoke(app, ["run"], obj=deps)

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "worker failed: OperationalError" in caplog.text
    assert "secret" not in caplog.text
    assert "nobody" not in caplog.text
    assert signal.getsignal(signal.SIGTERM) == previous


def test_lost_lock_connection_exits_1(cli, db, caplog):
    terminate = text(
        "SELECT pg_terminate_backend(pid) FROM pg_locks"
        " WHERE locktype = 'advisory' AND objid = :key AND granted"
    )

    class TerminatingClock(FixedClock):
        def sleep(self, seconds: float) -> None:
            with db.connect() as connection:
                connection.execute(terminate, {"key": SCHEDULE_LOCK_KEY})
                connection.commit()

    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    with caplog.at_level(logging.INFO):
        result = cli("run", client=fake.client(sleep=lambda _: None), clock=TerminatingClock(NOW))

    assert result.exit_code == 1
    assert "worker failed: OperationalError" in caplog.text


def test_sigterm_while_idle_exits_0_within_10_s(cli, db):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    start = time.monotonic()
    result = cli("run", client=fake.client(sleep=lambda _: None), clock=RealClock(far_future))
    elapsed = time.monotonic() - start
    timer.cancel()

    assert result.exit_code == 0
    assert elapsed < 10


def test_sigterm_masked_by_a_job_error_exits_0(cli, db):
    previous = signal.getsignal(signal.SIGTERM)

    def masked_route(request: httpx.Request) -> httpx.Response:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
        except Shutdown:
            pass
        raise RuntimeError("boom")

    fake = FakeFpl({"bootstrap-static/": masked_route})
    result = cli(
        "run", client=fake.client(sleep=lambda _: None, max_attempts=1), clock=RealClock(NOW)
    )

    assert result.exit_code == 0
    with Session(db) as session:
        assert session.exec(select(JobRun)).all() == []
    assert len(fake.requests) == 1
    assert signal.getsignal(signal.SIGTERM) == previous


def test_sigterm_during_job_rolls_back_and_exits_0(cli, db):
    previous = signal.getsignal(signal.SIGTERM)

    def killing_route(request: httpx.Request) -> httpx.Response:
        os.kill(os.getpid(), signal.SIGTERM)
        return httpx.Response(200, json=load("fixtures"))

    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": killing_route})
    result = cli(
        "run", client=fake.client(sleep=lambda _: None, max_attempts=1), clock=RealClock(NOW)
    )

    assert result.exit_code == 0
    with Session(db) as session:
        assert session.exec(select(Season)).all() == []
        assert session.exec(select(JobRun)).all() == []
    assert signal.getsignal(signal.SIGTERM) == previous


def test_run_without_tweet_source_logs_disabled_once(cli, db, caplog):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with caplog.at_level(logging.INFO):
            result = cli(
                "run", client=fake.client(sleep=lambda _: None), clock=RealClock(far_future)
            )
    finally:
        timer.cancel()

    assert result.exit_code == 0
    assert caplog.text.count("tweet ingest disabled") == 1
    with Session(db) as session:
        assert session.exec(select(TweetPoll)).all() == []


def test_sigterm_with_tweet_ingest_exits_within_10_s(cli, db):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    source = FakeSource(pages=[[]])
    tweet_ingest = TweetIngest(source_name="fake", list_id=1, make_source=lambda: source)

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    start = time.monotonic()
    try:
        result = cli(
            "run",
            client=fake.client(sleep=lambda _: None),
            clock=RealClock(far_future),
            tweet_ingest=tweet_ingest,
        )
    finally:
        elapsed = time.monotonic() - start
        timer.cancel()

    assert result.exit_code == 0
    assert elapsed < 10
    assert not any(t.name == "tweet-poller" and t.is_alive() for t in threading.enumerate())


def test_run_without_llm_logs_extraction_disabled_once(cli, db, caplog):
    from app.extraction.models import Extraction

    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with caplog.at_level(logging.INFO):
            result = cli(
                "run", client=fake.client(sleep=lambda _: None), clock=RealClock(far_future)
            )
    finally:
        timer.cancel()

    assert result.exit_code == 0
    assert caplog.text.count("extraction disabled") == 1
    with Session(db) as session:
        assert session.exec(select(Extraction)).all() == []


def test_run_without_langfuse_warns_once(cli, db, caplog):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    extraction = _extraction_runtime(ExtractionOutput(events=[]), tracing=None)

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with caplog.at_level(logging.WARNING):
            result = cli(
                "run",
                client=fake.client(sleep=lambda _: None),
                clock=RealClock(far_future),
                extraction=extraction,
            )
    finally:
        timer.cancel()

    assert result.exit_code == 0
    assert caplog.text.count("langfuse tracing disabled") == 1
    assert "LANGFUSE_PUBLIC_KEY" in caplog.text
    assert "LANGFUSE_SECRET_KEY" in caplog.text


def test_sigterm_with_extraction_exits_within_10_s(cli, db):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    _seed_tweet(db)
    extraction, llm = _extraction_runtime_and_fake(ExtractionOutput(events=[]))

    killer = _sigterm_when(lambda: len(_extractions(db)) >= 1)
    start = time.monotonic()
    result = cli(
        "run",
        client=fake.client(sleep=lambda _: None),
        clock=RealClock(far_future),
        extraction=extraction,
    )
    elapsed = time.monotonic() - start
    killer.join(timeout=1)

    assert result.exit_code == 0
    assert elapsed < 10
    assert len(llm.received_messages) == 1
    assert not any(t.name == "extractor" and t.is_alive() for t in threading.enumerate())


def test_run_stores_extraction_with_latency(cli, db):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    _seed_tweet(db, first_fetched_at=datetime.now(UTC) - timedelta(seconds=30))
    extraction = _extraction_runtime(ExtractionOutput(events=[]))

    killer = _sigterm_when(lambda: len(_extractions(db)) >= 1)
    result = cli(
        "run",
        client=fake.client(sleep=lambda _: None),
        clock=RealClock(far_future),
        extraction=extraction,
    )
    killer.join(timeout=1)

    assert result.exit_code == 0
    rows = _extractions(db)
    assert [(row.tweet_x_id, row.status) for row in rows] == [(1, "extracted")]
    assert rows[0].latency_seconds is not None
    assert 30 <= rows[0].latency_seconds < 60


def test_sigterm_with_extraction_blocked_in_a_call_exits_within_10_s(cli, db):
    # A blocked provider call cannot be interrupted: the daemon thread is abandoned after
    # the shared join deadline, but the worker itself must still exit within the bound.
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    _seed_tweet(db)
    block = threading.Event()
    extraction, llm = _extraction_runtime_and_fake(block)

    killer = _sigterm_when(lambda: len(llm.received_messages) >= 1)
    start = time.monotonic()
    try:
        result = cli(
            "run",
            client=fake.client(sleep=lambda _: None),
            clock=RealClock(far_future),
            extraction=extraction,
        )
    finally:
        elapsed = time.monotonic() - start
        killer.join(timeout=1)
        block.set()

    assert result.exit_code == 0
    assert elapsed < 10
    assert len(llm.received_messages) == 1
    assert _extractions(db) == []


def test_polls_continue_while_extraction_blocks(db):
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=D6,
                finished=False,
                data_checked=False,
            )
        )
        from app.tweets.models import Tweet

        session.add(
            Tweet(
                x_id=1,
                author_handle="reporter",
                text="Haaland starts.",
                created_at=D6 - timedelta(days=1),
                first_fetched_at=D6 - timedelta(days=1),
                source="fake",
                is_repost=False,
                is_reply=False,
                raw={},
            )
        )

    tweet_start = D6 - timedelta(minutes=5)
    tweet_end = D6 - timedelta(minutes=2)

    calls = {"n": 0}
    release_event = threading.Event()
    unblocked_after: dict[str, float | None] = {"seconds": None}

    def bootstrap_route(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            block_started = time.monotonic()
            release_event.wait(timeout=10)
            unblocked_after["seconds"] = time.monotonic() - block_started
        return httpx.Response(200, json=load("bootstrap-static"))

    fake = FakeFpl({"bootstrap-static/": bootstrap_route, "fixtures/": load("fixtures")})

    def watch_and_release() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with Session(db) as session:
                count = len(session.exec(select(TweetPoll)).all())
            if count >= 9:
                break
            time.sleep(0.02)
        release_event.set()
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)

    watcher = threading.Thread(target=watch_and_release, daemon=True)
    watcher.start()

    extraction_block = threading.Event()
    extraction, llm = _extraction_runtime_and_fake(extraction_block)
    poller_source = FakeSource(pages=[[]])

    def make_source_once_extraction_blocks() -> FakeSource:
        # Every poll then happens while the extractor is inside its blocked model call.
        assert _wait_until(lambda: len(llm.received_messages) >= 1)
        return poller_source

    tweet_ingest = TweetIngest(
        source_name="fake",
        list_id=1,
        make_source=make_source_once_extraction_blocks,
        clock=FakeClock(tweet_start, tweet_end),
    )
    deps = WorkerDeps(
        engine=db,
        client=fake.client(sleep=lambda _: None, max_attempts=1),
        league_ids_raw="1",
        clock=RealClock(tweet_start),
        tweet_ingest=tweet_ingest,
        extraction=extraction,
    )

    start = time.monotonic()
    try:
        result = CliRunner().invoke(app, ["run"], obj=deps)
    finally:
        # The extractor's fake call blocks forever otherwise: releasing it lets the
        # abandoned daemon thread unwind instead of outliving the test.
        extraction_block.set()
    elapsed = time.monotonic() - start
    watcher.join(timeout=1)

    assert result.exit_code == 0
    assert elapsed < 10
    assert calls["n"] == 2
    assert len(llm.received_messages) == 1
    assert _extractions(db) == []

    with Session(db) as session:
        rows = session.exec(
            select(TweetPoll).where(TweetPoll.outcome == "succeeded").order_by(TweetPoll.started_at)
        ).all()
    assert len(rows) == 9


def _embeddings(db):
    from app.retrieval.models import PostEmbedding

    with Session(db) as session:
        return session.exec(select(PostEmbedding)).all()


class QuickClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def sleep(self, seconds: float) -> None:
        self._offset += timedelta(seconds=seconds)
        time.sleep(0.01)


def test_run_without_key_logs_retrieval_indexing_disabled_once(cli, db, caplog):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with caplog.at_level(logging.INFO):
            result = cli(
                "run", client=fake.client(sleep=lambda _: None), clock=RealClock(far_future)
            )
    finally:
        timer.cancel()

    assert result.exit_code == 0
    assert caplog.text.count("retrieval indexing disabled") == 1
    assert _embeddings(db) == []


def test_run_with_indexing_logs_model_and_embeds_new_post(cli, db, caplog):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    _seed_tweet(db, first_fetched_at=datetime.now(UTC) - timedelta(seconds=30))
    indexing = indexing_runtime(FakeEmbedder())

    killer = _sigterm_when(lambda: len(_embeddings(db)) >= 1)
    with caplog.at_level(logging.INFO):
        result = cli(
            "run",
            client=fake.client(sleep=lambda _: None),
            clock=RealClock(far_future),
            indexing=indexing,
        )
    killer.join(timeout=1)

    assert result.exit_code == 0
    assert "retrieval indexing started: model=fake/embed" in caplog.text
    assert "retrieval indexing disabled" not in caplog.text
    rows = _embeddings(db)
    assert [(row.tweet_x_id, row.status, row.model) for row in rows] == [
        (1, "embedded", "fake/embed")
    ]
    assert not any(t.name == "indexer" and t.is_alive() for t in threading.enumerate())


class BlockingEmbedder(FakeEmbedder):
    def __init__(self, block: threading.Event) -> None:
        super().__init__()
        self._block = block

    def embed(self, texts):
        self.calls.append(list(texts))
        self._block.wait(timeout=30)
        return super().embed(texts)


def test_sigterm_with_embedder_blocked_in_a_call_exits_within_10_s(cli, db):
    # Mirrors the extraction case: a blocked embedding call is abandoned with its daemon
    # thread, and the worker still exits within the bound without storing the embedding.
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    _seed_tweet(db)
    block = threading.Event()
    embedder = BlockingEmbedder(block)

    killer = _sigterm_when(lambda: len(embedder.calls) >= 1)
    start = time.monotonic()
    try:
        result = cli(
            "run",
            client=fake.client(sleep=lambda _: None),
            clock=RealClock(far_future),
            indexing=indexing_runtime(embedder),
        )
        elapsed = time.monotonic() - start
        stored = _embeddings(db)  # read while the call is still blocked
    finally:
        killer.join(timeout=1)
        block.set()

    assert result.exit_code == 0
    assert elapsed < 10
    assert len(embedder.calls) == 1
    assert stored == []


def test_failing_embedder_does_not_stop_polls_or_extraction(db):
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=D6,
                finished=False,
                data_checked=False,
            )
        )
    _seed_tweet(db, first_fetched_at=D6 - timedelta(days=1))

    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    extraction, llm = _extraction_runtime_and_fake(ExtractionOutput(events=[]))
    embedder = AlwaysFailingEmbedder()
    indexing = indexing_runtime(embedder, clock=QuickClock())
    tweet_ingest = TweetIngest(
        source_name="fake",
        list_id=1,
        make_source=lambda: FakeSource(pages=[[]]),
        clock=FakeClock(D6 - timedelta(minutes=5), D6 - timedelta(minutes=2)),
    )

    def done() -> bool:
        with Session(db) as session:
            polls = len(session.exec(select(TweetPoll)).all())
        failed = [row for row in _embeddings(db) if row.status == "failed"]
        return polls >= 2 and len(_extractions(db)) >= 1 and len(failed) >= 1

    killer = _sigterm_when(done)
    deps = WorkerDeps(
        engine=db,
        client=fake.client(sleep=lambda _: None),
        league_ids_raw="1",
        clock=RealClock(D6 - timedelta(minutes=5)),
        tweet_ingest=tweet_ingest,
        extraction=extraction,
        indexing=indexing,
    )
    result = CliRunner().invoke(app, ["run"], obj=deps)
    killer.join(timeout=1)

    assert result.exit_code == 0
    with Session(db) as session:
        assert len(session.exec(select(TweetPoll)).all()) >= 2
    assert [row.status for row in _extractions(db)] == ["extracted"]
    assert {row.status for row in _embeddings(db)} == {"failed"}
    assert _embeddings(db)[0].error_class == "RuntimeError"


@pytest.mark.parametrize("model", ["x/unknown", "openai/gpt-6-luna"])
def test_worker_rejects_unpriced_embedding_model_at_start(monkeypatch, tmp_path, model):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-sentinel-value")
    monkeypatch.setenv("EMBEDDING_MODEL", model)

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1
    assert "EMBEDDING_MODEL" in result.stderr
    assert "sk-sentinel-value" not in result.stderr


def test_deps_enable_indexing_with_key(monkeypatch, tmp_path):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-sentinel-value")
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )

    indexing = worker_cli._deps_from_settings().indexing

    assert indexing is not None
    assert indexing.model == "openai/text-embedding-3-small"
    assert indexing.model in indexing.prices
    assert indexing.tracing is None


def test_deps_disable_indexing_without_key(monkeypatch, tmp_path):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )

    assert worker_cli._deps_from_settings().indexing is None


def test_worker_rejects_unknown_model_at_start(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-sentinel-value")
    monkeypatch.setenv("LLM_MODEL", "x/unknown-model")

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1
    assert "LLM_MODEL" in result.stderr
    assert "model_settings.toml" in result.stderr
    assert "sk-sentinel-value" not in result.stderr


def test_deps_disable_extraction_without_key_even_with_model(monkeypatch, tmp_path):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "google/gemini-3.1-flash-lite")
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )

    assert worker_cli._deps_from_settings().extraction is None


def test_deps_enable_extraction_with_key(monkeypatch, tmp_path):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-sentinel-value")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-6-luna")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "google/gemini-3.1-flash-lite")
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )

    extraction = worker_cli._deps_from_settings().extraction

    assert extraction is not None
    assert (extraction.provider, extraction.model) == ("openrouter", "openai/gpt-6-luna")
    assert extraction.fallback_model == "google/gemini-3.1-flash-lite"
    assert "openai/gpt-6-luna" in extraction.prices
    params = extraction.make_spec().chat_model._default_params
    assert params["models"] == ["openai/gpt-6-luna", "google/gemini-3.1-flash-lite"]


def test_worker_rejects_malformed_extraction_variable_naming_it_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USD_PLN_RATE", "4,05")

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert "USD_PLN_RATE" in result.stderr
    assert "4,05" not in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_worker_rejects_unreadable_prices_file_at_start(monkeypatch, tmp_path):
    import tomllib

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "google/gemini-3.1-flash-lite")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-sentinel-value")

    def broken_prices():
        raise tomllib.TOMLDecodeError("Invalid value (at line 1, column 5)")

    monkeypatch.setattr("app.extraction.service.load_prices", broken_prices)

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1
    assert "prices.toml" in result.stderr
    assert "TOMLDecodeError" in result.stderr
    assert "sk-sentinel-value" not in result.stderr


def test_polls_continue_while_a_deadline_snapshot_blocks(db):
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=D6,
                finished=False,
                data_checked=False,
            )
        )

    tweet_start = D6 - timedelta(minutes=5)
    tweet_end = D6 - timedelta(minutes=2)

    calls = {"n": 0}
    release_event = threading.Event()
    unblocked_after: dict[str, float | None] = {"seconds": None}

    def bootstrap_route(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            block_started = time.monotonic()
            release_event.wait(timeout=10)
            unblocked_after["seconds"] = time.monotonic() - block_started
        return httpx.Response(200, json=load("bootstrap-static"))

    fake = FakeFpl({"bootstrap-static/": bootstrap_route, "fixtures/": load("fixtures")})

    def watch_and_release() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with Session(db) as session:
                count = len(session.exec(select(TweetPoll)).all())
            if count >= 9:
                break
            time.sleep(0.02)
        release_event.set()
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)

    watcher = threading.Thread(target=watch_and_release, daemon=True)
    watcher.start()

    poller_source = FakeSource(pages=[[]])
    tweet_ingest = TweetIngest(
        source_name="fake",
        list_id=1,
        make_source=lambda: poller_source,
        clock=FakeClock(tweet_start, tweet_end),
    )
    deps = WorkerDeps(
        engine=db,
        client=fake.client(sleep=lambda _: None, max_attempts=1),
        league_ids_raw="1",
        clock=RealClock(tweet_start),
        tweet_ingest=tweet_ingest,
    )

    start = time.monotonic()
    result = CliRunner().invoke(app, ["run"], obj=deps)
    elapsed = time.monotonic() - start
    watcher.join(timeout=1)

    assert result.exit_code == 0
    assert elapsed < 10
    assert calls["n"] == 2
    assert unblocked_after["seconds"] is not None
    assert unblocked_after["seconds"] < 5

    with Session(db) as session:
        rows = session.exec(
            select(TweetPoll).where(TweetPoll.outcome == "succeeded").order_by(TweetPoll.started_at)
        ).all()
    assert len(rows) == 9
    started = [r.started_at for r in rows]
    assert started[0] == tweet_start
    assert all(
        (started[i + 1] - started[i]) == timedelta(seconds=20) for i in range(len(started) - 1)
    )


def test_worker_rejects_tweet_source_without_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TWEET_SOURCE", "x_api")
    monkeypatch.setenv("X_LIST_ID", "123")

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1
    assert "X_API_BEARER_TOKEN" in result.stderr


def test_status_shows_tweet_ingest_disabled(cli):
    result = cli("status")
    assert result.exit_code == 0
    assert "Tweet ingest: disabled" in result.stdout


def test_status_shows_tweet_ingest_never_polled(cli):
    tweet_ingest = TweetIngest(source_name="twitterapi_io", list_id=1, make_source=lambda: None)
    result = cli("status", tweet_ingest=tweet_ingest)
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "Tweet ingest:" in lines
    assert "  source: twitterapi_io" in lines
    assert "  last successful poll: never" in lines
    assert "  next poll: due now" in lines
    assert "  mode: sparse" in lines


def _seed_d6(db) -> None:
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=D6,
                finished=False,
                data_checked=False,
            )
        )


def _tweet_poll(started_at: datetime, outcome: str, **fields):
    from app.tweets.store import PollRecord

    return PollRecord(
        source="twitterapi_io",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        outcome=outcome,
        new_posts=fields.pop("new_posts", 0),
        **fields,
    )


def _tweet_status_lines(result) -> list[str]:
    lines = result.stdout.splitlines()
    start = lines.index("Tweet ingest:")
    end = lines.index("Extraction: disabled", start)
    return lines[start:end]


def test_status_shows_tweet_ingest_with_polls_in_window(cli, db):
    from app.tweets.store import write_poll

    _seed_d6(db)
    now = D6 - timedelta(minutes=45)
    write_poll(db, _tweet_poll(now - timedelta(seconds=20), "succeeded", new_posts=2))
    tweet_ingest = TweetIngest(source_name="twitterapi_io", list_id=1, make_source=lambda: None)
    result = cli("status", clock=FixedClock(now), tweet_ingest=tweet_ingest)
    assert result.exit_code == 0
    assert _tweet_status_lines(result) == [
        "Tweet ingest:",
        "  source: twitterapi_io",
        "  last successful poll: 2026-10-10T09:14:40Z",
        "  next poll: due now",
        "  mode: window",
    ]


def test_status_shows_last_success_before_a_later_failure(cli, db):
    from app.tweets.store import write_poll

    _seed_d6(db)
    now = D6 - timedelta(minutes=45)
    write_poll(db, _tweet_poll(now - timedelta(seconds=60), "succeeded", new_posts=1))
    write_poll(
        db,
        _tweet_poll(now - timedelta(seconds=10), "failed", error_class="SourceUnavailableError"),
    )
    tweet_ingest = TweetIngest(source_name="twitterapi_io", list_id=1, make_source=lambda: None)
    result = cli("status", clock=FixedClock(now), tweet_ingest=tweet_ingest)
    assert result.exit_code == 0
    assert _tweet_status_lines(result) == [
        "Tweet ingest:",
        "  source: twitterapi_io",
        "  last successful poll: 2026-10-10T09:14:00Z",
        "  next poll: 2026-10-10T09:15:10Z",
        "  mode: window",
    ]


def test_status_shows_rate_limited_next_poll_in_sparse_mode(cli, db):
    from app.tweets.store import write_poll

    _seed_d6(db)
    now = D6 - timedelta(hours=3)
    write_poll(
        db,
        _tweet_poll(
            now - timedelta(minutes=1),
            "rate_limited",
            error_class="SourceRateLimitedError",
            retry_after_seconds=600,
        ),
    )
    tweet_ingest = TweetIngest(source_name="twitterapi_io", list_id=1, make_source=lambda: None)
    result = cli("status", clock=FixedClock(now), tweet_ingest=tweet_ingest)
    assert result.exit_code == 0
    assert _tweet_status_lines(result) == [
        "Tweet ingest:",
        "  source: twitterapi_io",
        "  last successful poll: never",
        "  next poll: 2026-10-10T07:29:00Z",
        "  mode: sparse",
    ]


def test_status_shows_extraction_disabled(cli):
    result = cli("status")
    assert result.exit_code == 0
    assert "Extraction: disabled" in result.stdout


def test_status_shows_extraction_never(cli, db):
    extraction = _extraction_runtime()
    result = cli("status", extraction=extraction)
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "Extraction:" in lines
    assert "  model: fake:fake-model" in lines
    assert "  fallback: none" in lines
    assert "  posts waiting: 0" in lines
    assert "  failed posts: 0" in lines
    assert "  latest extraction: never" in lines


def test_status_shows_the_fallback_model(cli, db):
    extraction = _extraction_runtime()
    extraction = ExtractionRuntime(
        provider=extraction.provider,
        model=extraction.model,
        make_spec=extraction.make_spec,
        tracing=None,
        prices={},
        aliases=([], []),
        fallback_model="b/f",
    )
    result = cli("status", extraction=extraction)
    assert result.exit_code == 0
    assert "  fallback: b/f" in result.stdout.splitlines()


def test_status_shows_extraction_counts_and_latest(cli, db):
    from app.extraction.store import ExtractionRecord, save_extraction
    from app.tweets.models import Tweet

    with Session(db) as session:
        session.add(
            Tweet(
                x_id=1,
                author_handle="reporter",
                text="Haaland starts.",
                created_at=NOW,
                first_fetched_at=NOW,
                source="fake",
                is_repost=False,
                is_reply=False,
                raw={},
            )
        )
        session.add(
            Tweet(
                x_id=2,
                author_handle="reporter",
                text="Saka doubtful.",
                created_at=NOW,
                first_fetched_at=NOW,
                source="fake",
                is_repost=False,
                is_reply=False,
                raw={},
            )
        )
        session.commit()
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=1,
                status="failed",
                provider="fake",
                model="fake-model",
                prompt_version="v1",
                started_at=NOW,
                finished_at=NOW + timedelta(seconds=3),
                attempts=3,
                error_class="RuntimeError",
                latency_seconds=3.0,
            ),
            [],
        )

    extraction = _extraction_runtime()
    result = cli("status", extraction=extraction)
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "Extraction:" in lines
    assert "  model: fake:fake-model" in lines
    assert "  fallback: none" in lines
    assert "  posts waiting: 1" in lines  # x_id=2 has no extraction yet
    assert "  failed posts: 1" in lines
    assert "  latest extraction: 2026-09-26T00:00:03Z x_id=1 status=failed latency=3.0" in lines


def test_worker_help():
    result = subprocess.run(
        [sys.executable, "-m", "app.worker", "run", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0


def _delivery_row(db, key, kind, status, requested_at, accepted_at=None):
    from app.delivery.models import DeliveryLog

    with Session(db) as session:
        session.add(
            DeliveryLog(
                idempotency_key=key,
                kind=kind,
                channel="resend",
                title="t",
                text_body="b",
                status=status,
                attempts=1,
                requested_at=requested_at,
                accepted_at=accepted_at,
            )
        )
        session.commit()


def test_status_shows_delivery_disabled(cli):
    result = cli("status")
    assert result.exit_code == 0
    assert "Delivery: disabled" in result.stdout.splitlines()


def test_status_shows_delivery_line(cli, db):
    sent_at = NOW - timedelta(hours=2)
    _delivery_row(db, "presser:1", "presser", "sent", sent_at, sent_at)
    _delivery_row(db, "alert:1", "alert", "failed", NOW - timedelta(hours=1))
    _delivery_row(db, "alert:0", "alert", "failed", NOW - timedelta(hours=25))

    result = cli("status", delivery_channel="resend")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "Delivery: resend  last sent: 2026-09-25T22:00:00Z (presser)  failed in 24 h: 1" in lines
    assert lines.index("Extraction: disabled") < lines.index(
        "Delivery: resend  last sent: 2026-09-25T22:00:00Z (presser)  failed in 24 h: 1"
    )


def test_status_shows_delivery_line_with_no_rows(cli):
    result = cli("status", delivery_channel="resend")
    assert "Delivery: resend  last sent: never  failed in 24 h: 0" in result.stdout.splitlines()


def test_bad_delivery_provider_fails_worker_start(monkeypatch, tmp_path):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DELIVERY_PROVIDER", "smtp")
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )

    with pytest.raises(typer.Exit) as info:
        worker_cli._deps_from_settings()

    assert info.value.exit_code == 1


def test_worker_reports_bad_delivery_provider_naming_the_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DELIVERY_PROVIDER", "resend")

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert "RESEND_API_KEY" in result.stderr


def test_deps_carry_the_delivery_channel_name(monkeypatch, tmp_path):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )
    assert worker_cli._deps_from_settings().delivery_channel is None
    monkeypatch.setenv("DELIVERY_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_synthetic_key")
    monkeypatch.setenv("DELIVERY_EMAIL_TO", "owner@example.test")
    assert worker_cli._deps_from_settings().delivery_channel == "resend"


def _alerts_setup(rehearsal=None, slots=(120, 30)) -> AlertsSetup:
    from decimal import Decimal

    from app.alerts.config import AlertConfig
    from app.corroboration.runtime import sql_only_runtime
    from tests.delivery.fakes import FakeChannel

    return AlertsSetup(
        config=AlertConfig(slots, 3, Decimal("15"), rehearsal),
        make_channel=lambda: FakeChannel(["msg-1"]),
        make_corroboration=lambda: sql_only_runtime("test"),
    )


def test_status_shows_alerts_disabled_with_reason(cli):
    result = cli("status", alerts_disabled_reason="delivery disabled")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "Alerts: disabled (delivery disabled)" in lines
    assert lines.index("Delivery: disabled") < lines.index("Alerts: disabled (delivery disabled)")


def test_status_shows_alerts_line(cli, db):
    from app.alerts.store import record_alert
    from tests.alerts.helpers import deadline as alert_deadline

    _seed_d6(db)
    setup = _alerts_setup()

    first = cli("status", clock=FixedClock(D6 - timedelta(hours=5)), alerts=setup)
    assert "Alerts: next slot: digest 2026-10-10T08:00:00Z  last alert: never  failed: 0" in (
        first.stdout.splitlines()
    )

    real = alert_deadline()
    sent_at = D6 - timedelta(minutes=30)
    for key, kind, slot, status, at in (
        ("alert:2026/27:gw6:digest:120", "digest", 120, "sent", D6 - timedelta(hours=2)),
        ("alert:2026/27:gw6:news:30", "news", 30, "failed", sent_at),
    ):
        record_alert(
            db,
            key=key,
            deadline=real.__class__(real.key, D6, False, "2026/27", 6),
            kind=kind,
            slot_minutes=slot,
            trigger_x_id=None,
            as_of=at,
            status=status,
            delivery_log_id=None,
            posts=[],
            recorded_at=at,
        )
    later = cli("status", clock=FixedClock(D6 - timedelta(minutes=20)), alerts=setup)
    assert (
        "Alerts: breaking until 2026-10-10T10:00:00Z  last alert: news 2026-10-10T09:30:00Z"
        " failed  failed: 1"
    ) in later.stdout.splitlines()


def test_status_tweet_window_follows_the_alert_slots(cli, db):
    _seed_d6(db)
    tweet_ingest = TweetIngest(source_name="twitterapi_io", list_id=1, make_source=lambda: None)
    now = D6 - timedelta(minutes=120)

    with_alerts = cli(
        "status", clock=FixedClock(now), tweet_ingest=tweet_ingest, alerts=_alerts_setup()
    )
    without = cli("status", clock=FixedClock(now), tweet_ingest=tweet_ingest)

    assert "  mode: window" in with_alerts.stdout.splitlines()
    assert "  mode: sparse" in without.stdout.splitlines()


def test_run_starts_alerts_only_when_enabled(cli, db):
    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    seen: list[set[str]] = []

    def run_with(alerts):
        names: set[str] = set()
        timer_started = threading.Event()

        def watch() -> None:
            timer_started.set()
            _wait_until(lambda: any(t.name == "alerts" for t in threading.enumerate()), 1.5)
            names.update(t.name for t in threading.enumerate())
            os.kill(os.getpid(), signal.SIGTERM)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        result = cli(
            "run",
            client=fake.client(sleep=lambda _: None),
            clock=RealClock(far_future),
            alerts=alerts,
            alerts_disabled_reason=None if alerts else "ALERTS_ENABLED=false",
        )
        watcher.join(5)
        seen.append(names)
        return result

    enabled = run_with(_alerts_setup())
    disabled = run_with(None)

    assert enabled.exit_code == 0 and disabled.exit_code == 0
    assert "alerts" in seen[0]
    assert "alerts" not in seen[1]
    assert not any(t.name == "alerts" and t.is_alive() for t in threading.enumerate())


def test_worker_rejects_invalid_alert_slots_naming_the_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALERT_SLOTS_MINUTES", "30,120")

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert "ALERT_SLOTS_MINUTES" in result.stderr


def test_worker_rejects_overlapping_rehearsal_naming_the_variable(cli, db):
    _seed_d6(db)
    setup = _alerts_setup(rehearsal=D6 - timedelta(minutes=60))

    result = cli("run", clock=FixedClock(NOW), alerts=setup)

    assert result.exit_code == 1
    assert "ALERT_REHEARSAL_DEADLINE" in result.stderr


def test_rehearsal_not_written_to_gameweek_and_polls_fast(cli, db, monkeypatch):
    from app.worker import cli as worker_cli

    far_future = datetime(2027, 6, 1, tzinfo=UTC)
    rehearsal = far_future + timedelta(days=1)
    _seed_d6(db)
    with Session(db) as session:
        before = [(g.fpl_id, g.deadline_at) for g in session.exec(select(Gameweek)).all()]
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    captured = {}
    real_start = worker_cli.start_poller

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_start(*args, **kwargs)

    monkeypatch.setattr(worker_cli, "start_poller", spy)
    tweet_ingest = TweetIngest(
        source_name="fake", list_id=1, make_source=lambda: FakeSource(pages=[[]])
    )
    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        result = cli(
            "run",
            client=fake.client(sleep=lambda _: None),
            clock=RealClock(far_future),
            tweet_ingest=tweet_ingest,
            alerts=_alerts_setup(rehearsal=rehearsal),
        )
    finally:
        timer.cancel()

    assert result.exit_code == 0
    assert captured["window"] == timedelta(minutes=130)
    assert captured["extra_deadlines"] == (rehearsal,)
    with Session(db) as session:
        gameweeks = [(g.fpl_id, g.deadline_at) for g in session.exec(select(Gameweek)).all()]
    assert all(row in gameweeks for row in before)
    assert rehearsal not in [deadline for _, deadline in gameweeks]


def _alert_environment(monkeypatch, tmp_path, **extra):
    from app.core.settings import Settings
    from app.worker import cli as worker_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        worker_cli,
        "load_settings",
        lambda: Settings(_env_file=None, database_url="postgresql+psycopg://u@localhost/x"),
    )
    for name, value in extra.items():
        monkeypatch.setenv(name, value)
    return worker_cli


def _all_features(monkeypatch) -> None:
    for name, value in {
        "TWEET_SOURCE": "twitterapi_io",
        "TWITTERAPI_IO_KEY": "synthetic-key",
        "X_LIST_ID": "1",
        "OPENROUTER_API_KEY": "sk-sentinel-value",
        "LLM_MODEL": "openai/gpt-6-luna",
        "DELIVERY_PROVIDER": "resend",
        "RESEND_API_KEY": "re_synthetic_key",
        "DELIVERY_EMAIL_TO": "owner@example.test",
    }.items():
        monkeypatch.setenv(name, value)


def test_deps_carry_the_alert_setup_or_the_reason(monkeypatch, tmp_path):
    worker_cli = _alert_environment(monkeypatch, tmp_path)

    deps = worker_cli._deps_from_settings()
    assert deps.alerts is None and deps.alerts_disabled_reason == "delivery disabled"

    _all_features(monkeypatch)
    enabled = worker_cli._deps_from_settings()
    assert enabled.alerts is not None and enabled.alerts_disabled_reason is None
    assert enabled.alerts.config.slots == (120, 30)

    monkeypatch.setenv("ALERTS_ENABLED", "false")
    off = worker_cli._deps_from_settings()
    assert off.alerts is None and off.alerts_disabled_reason == "ALERTS_ENABLED=false"
