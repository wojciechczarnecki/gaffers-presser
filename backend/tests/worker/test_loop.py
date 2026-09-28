import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

import app.worker.loop as loop_module
from app.fpl.models import (
    DeadlineSnapshotPlayer,
    LeagueStanding,
    ManagerPick,
    PlayerGameweekResult,
)
from app.worker.jobs import Shutdown
from app.worker.loop import Worker
from app.worker.models import JobRun
from app.worker.schedule import Job
from tests.worker.sim import (
    ENTRY_IDS,
    LEAGUE_ID,
    FakeClock,
    SimulatedFpl,
    seed_done,
    trimmed_bootstrap,
    trimmed_live,
)

D6 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
D7 = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
SEASON = "2026/27"
HOUR = timedelta(hours=1)
QUARTER = timedelta(minutes=15)


def _run_log(engine, since: datetime) -> list[tuple[str, int | None, datetime]]:
    return [
        (r.job, r.gameweek_fpl_id, r.started_at) for r in _rows(engine) if r.started_at >= since
    ]


def _rows(engine, job: Job | None = None, gameweek=...):
    with Session(engine) as session:
        rows = session.exec(select(JobRun).order_by(JobRun.id)).all()
    if job is not None:
        rows = [r for r in rows if r.job == job.value]
    if gameweek is not ...:
        rows = [r for r in rows if r.gameweek_fpl_id == gameweek]
    return rows


def _worker(db, fpl, clock, league_ids=None, stop_event=None, heartbeat=None):
    return Worker(
        db,
        fpl.client(),
        league_ids or [],
        clock,
        stop_event=stop_event,
        heartbeat=heartbeat,
    )


def test_reference_sync_cadence(db):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    start = D6 - timedelta(hours=72)
    end = D6 - timedelta(hours=47)
    clock = FakeClock(start, end)
    fpl = SimulatedFpl(clock)
    worker = _worker(db, fpl, clock)
    try:
        worker.run()
    except Shutdown:
        pass

    hourly = [start + k * HOUR for k in range(25)]
    quarter_hourly = [D6 - timedelta(hours=48) + k * QUARTER for k in range(1, 4)]
    assert _run_log(db, since=start) == [
        ("reference_sync", None, t) for t in hourly + quarter_hourly
    ]


def test_deadline_snapshots_at_t30_and_t5(db):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    start = D6 - timedelta(hours=1)
    end = D6 + timedelta(minutes=1)
    clock = FakeClock(start, end)
    fpl = SimulatedFpl(clock)
    worker = _worker(db, fpl, clock)
    try:
        worker.run()
    except Shutdown:
        pass

    snap_rows = _rows(db, Job.deadline_snapshot, gameweek=6)
    times = sorted(r.started_at for r in snap_rows)
    assert times == [D6 - timedelta(minutes=30), D6 - timedelta(minutes=5)]
    assert all(r.outcome == "succeeded" for r in snap_rows)
    assert all(t < D6 for t in times)


def test_failed_snapshot_retried_until_deadline_then_missed(db, caplog):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    start = D6 - timedelta(hours=1)
    end = D6 + timedelta(hours=2)
    clock = FakeClock(start, end)
    failure_windows = {"bootstrap-static": [(D6 - timedelta(minutes=31), D6)]}
    fpl = SimulatedFpl(clock, failure_windows=failure_windows)
    worker = _worker(db, fpl, clock)
    with caplog.at_level(logging.INFO):
        try:
            worker.run()
        except Shutdown:
            pass

    snap_rows = _rows(db, Job.deadline_snapshot, gameweek=6)
    assert [(r.started_at, r.outcome) for r in snap_rows] == [
        (D6 - timedelta(minutes=30) + k * timedelta(minutes=1), "failed") for k in range(30)
    ]

    # The snapshot fetches bootstrap-static/ too, so reference syncs fail in the window.
    ref_rows = _rows(db, Job.reference_sync)
    in_window = [
        (r.started_at, r.outcome) for r in ref_rows if D6 - timedelta(minutes=31) <= r.started_at
    ]
    assert in_window[:3] == [
        (D6 - timedelta(minutes=30), "failed"),
        (D6 - timedelta(minutes=15), "failed"),
        (D6, "succeeded"),
    ]

    assert caplog.text.count("deadline snapshot missed: gameweek=6") == 1


def test_results_then_league_after_data_checked(db):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    finished_at = D6 + timedelta(hours=48)
    data_checked_at = D6 + timedelta(hours=70, minutes=10)
    start = D6 + timedelta(hours=70)
    end = D6 + timedelta(hours=95)
    clock = FakeClock(start, end)
    overrides = {6: [(finished_at, True, False), (data_checked_at, True, True)]}
    fpl = SimulatedFpl(clock, gameweek_overrides=overrides)
    worker = _worker(db, fpl, clock, league_ids=[])
    try:
        worker.run()
    except Shutdown:
        pass

    results_rows = _rows(db, Job.results_sync, gameweek=6)
    league_rows = _rows(db, Job.league_sync, gameweek=6)
    assert len(results_rows) == 1
    assert len(league_rows) == 1
    assert results_rows[0].outcome == "succeeded"
    assert league_rows[0].outcome == "succeeded"
    assert results_rows[0].started_at == league_rows[0].started_at
    assert results_rows[0].started_at > data_checked_at
    assert results_rows[0].id < league_rows[0].id


def test_failed_sync_retried_after_15_min(db):
    finished_before_start = D6 + timedelta(hours=70)
    start = finished_before_start
    end = start + timedelta(minutes=45)
    clock = FakeClock(start, end)
    overrides = {6: [(start - timedelta(seconds=1), True, True)]}
    failure_windows = {
        "bootstrap-static": [(start, start + timedelta(seconds=1))],
        "event-live": [(start + timedelta(minutes=15), start + timedelta(minutes=15, seconds=1))],
    }
    fpl = SimulatedFpl(clock, gameweek_overrides=overrides, failure_windows=failure_windows)
    worker = _worker(db, fpl, clock)
    try:
        worker.run()
    except Shutdown:
        pass

    ref_rows = _rows(db, Job.reference_sync)
    assert ref_rows[0].outcome == "failed"
    assert ref_rows[1].started_at - ref_rows[0].started_at == timedelta(minutes=15)
    assert ref_rows[1].outcome == "succeeded"

    results_rows = _rows(db, Job.results_sync, gameweek=6)
    assert [r.outcome for r in results_rows] == ["failed", "succeeded"]
    assert results_rows[1].started_at - results_rows[0].started_at == timedelta(minutes=15)

    league_rows = _rows(db, Job.league_sync, gameweek=6)
    assert len(league_rows) == 1
    assert league_rows[0].outcome == "succeeded"


def test_catch_up_on_empty_database_and_restart(db):
    start = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=70)
    clock = FakeClock(start, end)
    fpl = SimulatedFpl(clock)
    worker = _worker(db, fpl, clock)
    try:
        worker.run()
    except Shutdown:
        pass

    catch_up = [
        (r.job, r.gameweek_fpl_id)
        for r in _rows(db)
        if r.job in (Job.results_sync.value, Job.league_sync.value)
    ]
    assert catch_up == [
        ("results_sync", 1),
        ("league_sync", 1),
        ("results_sync", 2),
        ("league_sync", 2),
        ("results_sync", 3),
        ("league_sync", 3),
        ("results_sync", 4),
        ("league_sync", 4),
        ("results_sync", 5),
        ("league_sync", 5),
    ]
    assert all(r.outcome == "succeeded" for r in _rows(db))

    second_clock = FakeClock(start + timedelta(minutes=90), start + timedelta(minutes=100))
    second_fpl = SimulatedFpl(second_clock)
    second_worker = _worker(db, second_fpl, second_clock)
    before = len(_rows(db))
    try:
        second_worker.run()
    except Shutdown:
        pass
    after_rows = _rows(db)
    new_rows = after_rows[before:]
    assert new_rows
    assert all(r.job == "reference_sync" for r in new_rows)


def test_restart_after_downtime(db, caplog):
    finished_at = D6 + timedelta(hours=48)
    data_checked_at = D6 + timedelta(hours=70, minutes=10)
    overrides = {6: [(finished_at, True, False), (data_checked_at, True, True)]}

    first_clock = FakeClock(D6 - timedelta(hours=3, minutes=5), D6 - timedelta(hours=3))
    first_fpl = SimulatedFpl(first_clock, gameweek_overrides=overrides)
    first_worker = _worker(db, first_fpl, first_clock)
    try:
        first_worker.run()
    except Shutdown:
        pass

    restart_at = D6 + timedelta(hours=70, minutes=30)
    second_clock = FakeClock(restart_at, restart_at + timedelta(minutes=10))
    second_fpl = SimulatedFpl(second_clock, gameweek_overrides=overrides)
    second_worker = _worker(db, second_fpl, second_clock)
    with caplog.at_level(logging.INFO):
        try:
            second_worker.run()
        except Shutdown:
            pass

    results_rows = _rows(db, Job.results_sync, gameweek=6)
    league_rows = _rows(db, Job.league_sync, gameweek=6)
    assert len(results_rows) == 1
    assert len(league_rows) == 1
    assert _rows(db, Job.deadline_snapshot, gameweek=6) == []
    assert "deadline snapshot missed: gameweek=6" in caplog.text


def test_no_future_deadline_daily_reference_only(db):
    seed_done(db, SEASON, list(range(1, 39)), datetime(2027, 5, 1, tzinfo=UTC))
    start = datetime(2027, 6, 1, tzinfo=UTC)
    end = start + timedelta(days=3)
    clock = FakeClock(start, end)
    fpl = SimulatedFpl(clock)
    worker = _worker(db, fpl, clock)
    try:
        worker.run()
    except Shutdown:
        pass

    assert _run_log(db, since=start) == [
        ("reference_sync", None, start + k * timedelta(hours=24)) for k in range(3)
    ]


_JOB_ORDER = {
    Job.deadline_snapshot.value: 0,
    Job.reference_sync.value: 1,
    Job.results_sync.value: 2,
    Job.league_sync.value: 3,
}


def test_simulated_gameweek_sequence(db):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    finished_at = D6 + timedelta(hours=48)
    data_checked_at = D6 + timedelta(hours=70, minutes=10)
    start = D6 - timedelta(hours=72)
    end = D6 + timedelta(hours=72)
    clock = FakeClock(start, end)
    overrides = {6: [(finished_at, True, False), (data_checked_at, True, True)]}
    fpl = SimulatedFpl(clock, gameweek_overrides=overrides)
    worker = _worker(db, fpl, clock, league_ids=[LEAGUE_ID])
    try:
        worker.run()
    except Shutdown:
        pass

    # Built by plain arithmetic: hourly to D-48 h, every 15 min to D, hourly after D;
    # snapshots at T-30 and T-5; results then league sync at the first reference sync
    # after data_checked (D+71 h).
    reference = (
        [start + k * HOUR for k in range(25)]
        + [D6 - timedelta(hours=48) + k * QUARTER for k in range(1, 193)]
        + [D6 + k * HOUR for k in range(1, 72)]
    )
    expected = [("reference_sync", None, t) for t in reference]
    expected += [
        ("deadline_snapshot", 6, D6 - timedelta(minutes=30)),
        ("deadline_snapshot", 6, D6 - timedelta(minutes=5)),
        ("results_sync", 6, D6 + timedelta(hours=71)),
        ("league_sync", 6, D6 + timedelta(hours=71)),
    ]
    expected.sort(key=lambda row: (row[2], _JOB_ORDER[row[0]]))
    assert len(expected) == 292
    assert _run_log(db, since=start) == expected
    assert all(r.outcome == "succeeded" for r in _rows(db))

    with Session(db) as session:
        snapshot_players = session.exec(
            select(DeadlineSnapshotPlayer.player_fpl_id).where(
                DeadlineSnapshotPlayer.season == SEASON,
                DeadlineSnapshotPlayer.gameweek_fpl_id == 6,
            )
        ).all()
        result_players = session.exec(
            select(PlayerGameweekResult.player_fpl_id).where(
                PlayerGameweekResult.season == SEASON,
                PlayerGameweekResult.gameweek_fpl_id == 6,
            )
        ).all()
        standings = session.exec(
            select(LeagueStanding.gameweek_fpl_id, LeagueStanding.entry_id).where(
                LeagueStanding.season == SEASON, LeagueStanding.league_fpl_id == LEAGUE_ID
            )
        ).all()
        picked_entries = session.exec(
            select(ManagerPick.entry_id).where(
                ManagerPick.season == SEASON, ManagerPick.gameweek_fpl_id == 6
            )
        ).all()

    assert sorted(snapshot_players) == sorted(el["id"] for el in trimmed_bootstrap()["elements"])
    assert sorted(result_players) == sorted(el["id"] for el in trimmed_live()["elements"])
    assert sorted(standings) == [(6, entry_id) for entry_id in ENTRY_IDS]
    assert set(picked_entries) == set(ENTRY_IDS)


def test_simulated_gameweek_logs_carry_no_private_data(db, caplog):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    finished_at = D6 + timedelta(hours=48)
    data_checked_at = D6 + timedelta(hours=70, minutes=10)
    start = D6 - timedelta(hours=72)
    end = D6 + timedelta(hours=72)
    clock = FakeClock(start, end)
    overrides = {6: [(finished_at, True, False), (data_checked_at, True, True)]}
    fpl = SimulatedFpl(clock, gameweek_overrides=overrides)
    from tests.worker.sim import ENTRY_IDS, LEAGUE_ID

    worker = _worker(db, fpl, clock, league_ids=[LEAGUE_ID])
    with caplog.at_level(logging.DEBUG):
        try:
            worker.run()
        except Shutdown:
            pass

    assert str(LEAGUE_ID) not in caplog.text
    for entry_id in ENTRY_IDS:
        assert str(entry_id) not in caplog.text
        assert f"Synthetic Manager {entry_id}" not in caplog.text
        assert f"Synthetic XI {entry_id}" not in caplog.text

    started = [line for line in caplog.text.splitlines() if "job started:" in line]
    finished = [line for line in caplog.text.splitlines() if "job finished:" in line]
    rows = [r for r in _rows(db) if r.started_at >= start]
    assert len(started) == len(rows)
    assert len(finished) == len(rows)


def test_heartbeat_failure_ends_run(db):
    calls = {"n": 0}

    def heartbeat():
        calls["n"] += 1
        if calls["n"] == 2:
            raise OperationalError("SELECT 1", None, Exception("connection lost"))

    start = D6 - timedelta(hours=72)
    end = start + timedelta(hours=1)
    clock = FakeClock(start, end)
    fpl = SimulatedFpl(clock)
    worker = _worker(db, fpl, clock, heartbeat=heartbeat)

    with pytest.raises(OperationalError):
        worker.run()

    assert calls["n"] == 2
    assert len(_rows(db, Job.reference_sync)) == 1


def test_state_load_failure_is_logged_and_retried_after_60_s(db, caplog, monkeypatch):
    seed_done(db, SEASON, [1, 2, 3, 4, 5], D6 - timedelta(hours=200))
    real_load_state = loop_module.load_state
    calls = {"n": 0}

    def flaky_load_state(engine):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT", None, Exception("password=hunter2"))
        return real_load_state(engine)

    monkeypatch.setattr(loop_module, "load_state", flaky_load_state)

    class RecordingClock(FakeClock):
        def __init__(self, start: datetime, end: datetime) -> None:
            super().__init__(start, end)
            self.sleeps: list[float] = []

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            super().sleep(seconds)

    start = D6 - timedelta(hours=72)
    clock = RecordingClock(start, start + timedelta(hours=1, minutes=30))
    fpl = SimulatedFpl(clock)
    worker = _worker(db, fpl, clock)
    with caplog.at_level(logging.INFO):
        try:
            worker.run()
        except Shutdown:
            pass

    assert clock.sleeps[:2] == [60, 3540]
    assert caplog.text.count("state load failed: OperationalError") == 1
    assert "hunter2" not in caplog.text
    assert calls["n"] >= 3
    assert _run_log(db, since=start) == [
        ("reference_sync", None, start),
        ("reference_sync", None, start + HOUR),
    ]


def test_stop_event_ends_run_after_action(db):
    stop_event = threading.Event()

    start = D6 - timedelta(hours=72)
    end = start + timedelta(hours=1)
    clock = FakeClock(start, end)
    fpl = SimulatedFpl(clock)

    original_route = fpl._bootstrap_route

    def stopping_route(request):
        response = original_route(request)
        stop_event.set()
        return response

    fpl._bootstrap_route = stopping_route

    worker = _worker(db, fpl, clock, stop_event=stop_event)

    with pytest.raises(Shutdown):
        worker.run()

    assert len(_rows(db)) == 1
    assert _rows(db)[0].outcome == "succeeded"
