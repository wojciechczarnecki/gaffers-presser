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
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.db.engine import make_engine
from app.db.locks import SCHEDULE_LOCK_KEY
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import ExtractionOutput
from app.extraction.service import ExtractionRuntime
from app.fpl.models import Gameweek, Season
from app.tweets.models import TweetPoll
from app.worker.cli import TweetIngest, WorkerDeps, app
from app.worker.jobs import Shutdown
from app.worker.models import JobRun
from app.worker.schedule import Job
from tests.conftest import BACKEND_DIR, held_advisory_lock
from tests.extraction.fakes import FakeChatModel
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load
from tests.tweets.fakes import FakeSource
from tests.worker.sim import FakeClock

EXTRACTION_VARIABLE = re.compile(r"(LLM_.*|LANGFUSE_.*|.*_API_KEY|USD_PLN_RATE)")
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
        *args, client=None, league_ids_raw="1", clock=None, tweet_ingest=None, extraction=None
    ):
        deps = WorkerDeps(
            engine=db,
            client=client or FakeFpl({}).client(sleep=lambda _: None),
            league_ids_raw=league_ids_raw,
            clock=clock or FixedClock(NOW),
            tweet_ingest=tweet_ingest,
            extraction=extraction,
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
