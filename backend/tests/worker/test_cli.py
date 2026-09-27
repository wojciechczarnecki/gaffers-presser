import os
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

from app.fpl.models import Gameweek, Season
from app.worker.cli import WorkerDeps, app
from app.worker.jobs import Shutdown
from app.worker.models import JobRun
from app.worker.schedule import Job
from tests.conftest import BACKEND_DIR
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)
D6 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


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
    def invoke(*args, client=None, league_ids_raw="1", clock=None):
        deps = WorkerDeps(
            engine=db,
            client=client or FakeFpl({}).client(sleep=lambda _: None),
            league_ids_raw=league_ids_raw,
            clock=clock or FixedClock(NOW),
        )
        return CliRunner().invoke(app, list(args), obj=deps)

    return invoke


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
    assert "deadline_snapshot gameweek=6: 2026-10-10T09:30:00Z" in result.stdout


def test_second_worker_waits_for_schedule_lock(cli, db):
    lock_conn = db.connect().execution_options(isolation_level="AUTOCOMMIT")
    lock_conn.execute(text("SELECT pg_advisory_lock(8002001)"))

    released_at = NOW + timedelta(minutes=10)
    clock = ReleasingClock(
        NOW,
        release_after=timedelta(minutes=10),
        end_after=timedelta(hours=1),
        release_fn=lambda: lock_conn.execute(text("SELECT pg_advisory_unlock(8002001)")),
    )
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    result = cli("run", client=fake.client(sleep=lambda _: None), clock=clock)

    assert result.exit_code == 0
    with Session(db) as session:
        rows = session.exec(select(JobRun)).all()
    assert rows
    assert all(r.started_at >= released_at for r in rows)
    lock_conn.close()


def test_sigterm_while_waiting_for_schedule_lock_exits_0(cli, db):
    lock_conn = db.connect().execution_options(isolation_level="AUTOCOMMIT")
    lock_conn.execute(text("SELECT pg_advisory_lock(8002001)"))

    timer = threading.Timer(1, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    start = time.monotonic()
    result = cli("run", clock=RealClock(NOW))
    elapsed = time.monotonic() - start
    timer.cancel()

    assert result.exit_code == 0
    assert elapsed < 10
    with Session(db) as session:
        assert session.exec(select(JobRun)).all() == []

    lock_conn.execute(text("SELECT pg_advisory_unlock(8002001)"))
    lock_conn.close()


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


def test_worker_help():
    result = subprocess.run(
        [sys.executable, "-m", "app.worker", "run", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0
