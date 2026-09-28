import logging
import threading
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.db.locks import JOB_LOCK_KEY
from app.fpl.models import DeadlineSnapshotPlayer, Gameweek
from app.worker.jobs import Shutdown, run_job
from app.worker.models import JobRun
from app.worker.schedule import Job, PlannedAction
from tests.conftest import held_advisory_lock, wait_for_lock_waiter
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)
D6 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def _ticking(start: datetime, step: timedelta = timedelta(seconds=12.5)):
    calls = {"n": 0}

    def now() -> datetime:
        value = start + calls["n"] * step
        calls["n"] += 1
        return value

    return now


def _all_tables_empty_except_job_run(engine) -> bool:
    with Session(engine) as session:
        for name, table in SQLModel.metadata.tables.items():
            if name == "job_run":
                continue
            if session.execute(table.select()).first() is not None:
                return False
    return True


def test_successful_job_logs_run(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    record = run_job(db, client, [], action, now_fn=lambda: NOW, stop_event=threading.Event())

    assert record.outcome == "succeeded"
    with Session(db) as session:
        rows = session.exec(select(JobRun)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.job == "reference_sync"
        assert row.season == "2026/27"
        assert row.gameweek_fpl_id is None
        assert row.outcome == "succeeded"
        assert row.error_class is None
        assert row.started_at.utcoffset() == timedelta(0)
        assert row.finished_at.utcoffset() == timedelta(0)


def test_failed_job_is_logged_and_rolled_back(db):
    fake = FakeFpl(
        {
            "bootstrap-static/": load("bootstrap-static"),
            "fixtures/": lambda request: httpx.Response(503),
        }
    )
    client = fake.client(sleep=lambda _: None, max_attempts=1)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    record = run_job(db, client, [], action, now_fn=lambda: NOW, stop_event=threading.Event())

    assert record.outcome == "failed"
    assert _all_tables_empty_except_job_run(db)
    with Session(db) as session:
        rows = session.exec(select(JobRun)).all()
        assert len(rows) == 1
        assert rows[0].outcome == "failed"
        assert rows[0].error_class == "FplUnavailableError"


def _run_job_in_thread(db, client, action, now_fn, result_box) -> threading.Thread:
    def run() -> None:
        result_box["record"] = run_job(
            db, client, [], action, now_fn=now_fn, stop_event=threading.Event()
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_worker_job_waits_for_running_job(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)
    clock = {"now": NOW}
    result_box: dict = {}

    with held_advisory_lock(db, JOB_LOCK_KEY) as release:
        thread = _run_job_in_thread(db, client, action, lambda: clock["now"], result_box)
        wait_for_lock_waiter(db, JOB_LOCK_KEY)
        with Session(db) as session:
            assert session.exec(select(JobRun)).all() == []
        clock["now"] = NOW + timedelta(minutes=20)
        release()
        thread.join(timeout=10)

    assert not thread.is_alive()
    record = result_box["record"]
    assert record.outcome == "succeeded"
    assert record.started_at == NOW + timedelta(minutes=20)


def test_snapshot_waiting_past_the_deadline_fails_the_guard(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    action = PlannedAction(
        at=D6 - timedelta(minutes=5), job=Job.deadline_snapshot, gameweek=6, season="2026/27"
    )
    clock = {"now": D6 - timedelta(minutes=5)}
    result_box: dict = {}

    with held_advisory_lock(db, JOB_LOCK_KEY) as release:
        thread = _run_job_in_thread(db, client, action, lambda: clock["now"], result_box)
        wait_for_lock_waiter(db, JOB_LOCK_KEY)
        clock["now"] = D6 + timedelta(minutes=15)
        release()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert result_box["record"].outcome == "failed"
    with Session(db) as session:
        assert session.exec(select(DeadlineSnapshotPlayer)).all() == []
        row = session.exec(select(JobRun)).one()
        assert row.error_class == "JobError"
        assert row.started_at == D6 + timedelta(minutes=15)


def test_error_after_stop_request_raises_shutdown(db):
    stop_event = threading.Event()

    def failing_route(request: httpx.Request) -> httpx.Response:
        stop_event.set()
        return httpx.Response(503)

    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": failing_route})
    client = fake.client(sleep=lambda _: None, max_attempts=1)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    try:
        run_job(db, client, [], action, now_fn=lambda: NOW, stop_event=stop_event)
        raised = False
    except Shutdown:
        raised = True

    assert raised
    assert _all_tables_empty_except_job_run(db)
    with Session(db) as session:
        assert session.exec(select(JobRun)).all() == []


def test_job_logged_at_start_and_end(db, caplog):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    with caplog.at_level(logging.INFO):
        run_job(db, client, [], action, now_fn=_ticking(NOW), stop_event=threading.Event())

    started_lines = [
        line for line in caplog.text.splitlines() if "job started: job=reference_sync" in line
    ]
    finished_lines = [
        line for line in caplog.text.splitlines() if "job finished: job=reference_sync" in line
    ]
    assert len(started_lines) == 1
    assert len(finished_lines) == 1
    assert "gameweek=-" in started_lines[0]
    assert "gameweek=-" in finished_lines[0]
    assert "outcome=succeeded" in finished_lines[0]
    assert "duration=12.5s" in finished_lines[0]
    assert "error=" not in finished_lines[0]


def test_failed_job_logged_with_error_class_only(db, caplog):
    fake = FakeFpl(
        {
            "bootstrap-static/": load("bootstrap-static"),
            "fixtures/": lambda request: httpx.Response(503, text="upstream secret detail"),
        }
    )
    client = fake.client(sleep=lambda _: None, max_attempts=1)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    with caplog.at_level(logging.INFO):
        run_job(db, client, [], action, now_fn=_ticking(NOW), stop_event=threading.Event())

    finished_lines = [line for line in caplog.text.splitlines() if "job finished:" in line]
    assert len(finished_lines) == 1
    line = finished_lines[0]
    assert "job=reference_sync gameweek=-" in line
    assert "outcome=failed" in line
    assert "duration=12.5s" in line
    assert line.endswith("error=FplUnavailableError")
    assert "upstream secret detail" not in caplog.text
    assert "503" not in line


def test_log_write_failure_is_logged_and_job_data_kept(db, caplog):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    with db.begin() as connection:
        connection.execute(
            text("ALTER TABLE job_run ADD CONSTRAINT reject_all CHECK (false) NOT VALID")
        )
    try:
        with caplog.at_level(logging.INFO):
            record = run_job(
                db, client, [], action, now_fn=lambda: NOW, stop_event=threading.Event()
            )
    finally:
        with db.begin() as connection:
            connection.execute(text("ALTER TABLE job_run DROP CONSTRAINT reject_all"))

    assert record.outcome == "succeeded"
    assert "job run log write failed: IntegrityError" in caplog.text
    assert "reject_all" not in caplog.text
    with Session(db) as session:
        assert len(session.exec(select(Gameweek)).all()) == 38
        assert session.exec(select(JobRun)).all() == []
