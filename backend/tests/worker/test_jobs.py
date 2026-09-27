import logging
import threading
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app.worker.jobs import Shutdown, run_job
from app.worker.models import JobRun
from app.worker.schedule import Job, PlannedAction
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)


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


def test_worker_job_waits_for_running_job(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    action = PlannedAction(at=NOW, job=Job.reference_sync, gameweek=None, season=None)

    lock_session = Session(db)
    lock_session.begin()
    lock_session.execute(text("SELECT pg_advisory_xact_lock(8002002)"))

    result_box: dict = {}

    def run() -> None:
        result_box["record"] = run_job(
            db, client, [], action, now_fn=lambda: NOW, stop_event=threading.Event()
        )

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=1)

    assert thread.is_alive()
    with Session(db) as session:
        assert session.exec(select(JobRun)).all() == []

    lock_session.commit()
    lock_session.close()

    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result_box["record"].outcome == "succeeded"


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
        run_job(db, client, [], action, now_fn=lambda: NOW, stop_event=threading.Event())

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
