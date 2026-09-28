import threading
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select
from typer.testing import CliRunner

from app.db.locks import JOB_LOCK_KEY
from app.fpl.cli import Deps, app
from app.fpl.models import DeadlineSnapshotPlayer, Gameweek
from tests.conftest import held_advisory_lock, wait_for_lock_waiter
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)
D6 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def _run_in_thread(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def test_cli_job_waits_for_running_job(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    deps = Deps(
        engine=db, client=fake.client(sleep=lambda _: None), league_ids_raw="", clock=lambda: NOW
    )
    result_box: dict = {}

    with held_advisory_lock(db, JOB_LOCK_KEY) as release:
        thread = _run_in_thread(
            lambda: result_box.update(result=CliRunner().invoke(app, ["reference-sync"], obj=deps))
        )
        wait_for_lock_waiter(db, JOB_LOCK_KEY)
        with Session(db) as session:
            assert session.exec(select(Gameweek)).all() == []
        release()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert result_box["result"].exit_code == 0
    with Session(db) as session:
        assert len(session.exec(select(Gameweek)).all()) == 38


def test_cli_snapshot_reads_the_clock_after_the_lock_wait(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    clock = {"now": D6 - timedelta(minutes=10)}
    deps = Deps(
        engine=db,
        client=fake.client(sleep=lambda _: None),
        league_ids_raw="",
        clock=lambda: clock["now"],
    )
    result_box: dict = {}

    with held_advisory_lock(db, JOB_LOCK_KEY) as release:
        thread = _run_in_thread(
            lambda: result_box.update(
                result=CliRunner().invoke(app, ["deadline-snapshot", "--gameweek", "6"], obj=deps)
            )
        )
        wait_for_lock_waiter(db, JOB_LOCK_KEY)
        clock["now"] = D6 + timedelta(minutes=10)
        release()
        thread.join(timeout=10)

    assert not thread.is_alive()
    result = result_box["result"]
    assert result.exit_code == 1
    assert "gameweek 6 deadline has passed" in result.output
    with Session(db) as session:
        assert session.exec(select(DeadlineSnapshotPlayer)).all() == []
