from datetime import UTC, datetime, timedelta

from app.fpl.models import Gameweek, Season
from app.worker.models import JobRun
from app.worker.schedule import Job
from app.worker.store import latest_runs_by_job, load_state

D = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def test_load_state_on_empty_database(db_session):
    state = load_state(db_session.get_bind())
    assert state.season is None
    assert state.gameweeks == []
    assert state.latest == {}
    assert state.latest_success == {}


def _job_run(job, season, gw, started_at, outcome):
    return JobRun(
        job=job.value,
        season=season,
        gameweek_fpl_id=gw,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=5),
        outcome=outcome,
    )


def test_load_state_picks_latest_season_and_scoped_runs(db_session):
    db_session.add_all([Season(label="2025/26"), Season(label="2026/27")])
    db_session.flush()
    db_session.add_all(
        [
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=D,
                finished=False,
                data_checked=False,
            ),
            Gameweek(
                season="2025/26",
                fpl_id=6,
                name="GW6",
                deadline_at=D - timedelta(days=365),
                finished=True,
                data_checked=True,
            ),
        ]
    )
    db_session.add_all(
        [
            _job_run(Job.reference_sync, None, None, D - timedelta(hours=2), "failed"),
            _job_run(Job.reference_sync, None, None, D - timedelta(hours=1), "succeeded"),
            _job_run(Job.results_sync, "2025/26", 6, D - timedelta(days=400), "succeeded"),
            _job_run(Job.results_sync, "2026/27", 6, D - timedelta(hours=3), "failed"),
            _job_run(Job.results_sync, "2026/27", 6, D - timedelta(hours=2), "succeeded"),
        ]
    )
    db_session.commit()

    state = load_state(db_session.get_bind())
    assert state.season == "2026/27"
    assert {g.fpl_id for g in state.gameweeks} == {6}

    ref = state.latest[(Job.reference_sync, None)]
    assert ref.outcome == "succeeded"
    assert ref.started_at == D - timedelta(hours=1)

    results = state.latest[(Job.results_sync, 6)]
    assert results.started_at == D - timedelta(hours=2)
    assert results.outcome == "succeeded"

    results_success = state.latest_success[(Job.results_sync, 6)]
    assert results_success.started_at == D - timedelta(hours=2)

    assert (Job.results_sync, 6) not in state.latest_success or state.latest_success[
        (Job.results_sync, 6)
    ].started_at != D - timedelta(days=400)


def test_load_state_ignores_gameweek_runs_of_a_previous_season(db_session):
    db_session.add_all([Season(label="2025/26"), Season(label="2026/27")])
    db_session.flush()
    db_session.add_all(
        [
            _job_run(Job.results_sync, "2025/26", 7, D - timedelta(days=300), "succeeded"),
            _job_run(Job.league_sync, "2025/26", 7, D - timedelta(days=300), "succeeded"),
            _job_run(Job.deadline_snapshot, "2025/26", 7, D - timedelta(days=301), "failed"),
        ]
    )
    db_session.commit()

    state = load_state(db_session.get_bind())
    assert state.season == "2026/27"
    for job in (Job.results_sync, Job.league_sync, Job.deadline_snapshot):
        assert (job, 7) not in state.latest
        assert (job, 7) not in state.latest_success


def test_latest_runs_by_job(db_session):
    db_session.add_all(
        [
            _job_run(Job.reference_sync, None, None, D - timedelta(hours=1), "succeeded"),
            _job_run(Job.league_sync, "2026/27", 6, D - timedelta(hours=2), "failed"),
        ]
    )
    db_session.commit()

    latest = latest_runs_by_job(db_session.get_bind())
    assert latest[Job.reference_sync].outcome == "succeeded"
    assert latest[Job.league_sync].outcome == "failed"
    assert latest[Job.deadline_snapshot] is None
    assert latest[Job.results_sync] is None
