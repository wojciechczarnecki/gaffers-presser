from datetime import UTC, datetime, timedelta

from app.worker.schedule import (
    GameweekState,
    Job,
    PlannedAction,
    RunRecord,
    ScheduleState,
    due_actions,
    missed_snapshot,
    plan,
)

SEASON = "2026/27"
D6 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
D5 = datetime(2026, 9, 18, 17, 30, tzinfo=UTC)


def _state(
    gameweeks: list[GameweekState],
    latest: dict | None = None,
    latest_success: dict | None = None,
) -> ScheduleState:
    return ScheduleState(
        season=SEASON,
        gameweeks=gameweeks,
        latest=latest or {},
        latest_success=latest_success or {},
    )


def _gw(fpl_id: int, deadline_at: datetime, finished: bool = False, data_checked: bool = False):
    return GameweekState(
        fpl_id=fpl_id, deadline_at=deadline_at, finished=finished, data_checked=data_checked
    )


def _run(job: Job, gw: int | None, started_at: datetime, outcome: str, duration_s: int = 5):
    return RunRecord(
        job=job,
        gameweek=gw,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=duration_s),
        outcome=outcome,
    )


def _next_reference(state, now):
    return next(a for a in plan(state, now) if a.job == Job.reference_sync)


def test_empty_state():
    state = _state([])
    actions = plan(state, D6 - timedelta(hours=100))
    assert actions == [
        PlannedAction(
            at=D6 - timedelta(hours=100), job=Job.reference_sync, gameweek=None, season=SEASON
        )
    ]
    due = due_actions(state, D6 - timedelta(hours=100))
    assert due == actions


def test_reference_cadence_empty_log_is_now():
    state = _state([_gw(6, D6)])
    now = D6 - timedelta(hours=72)
    action = _next_reference(state, now)
    assert action.at == now


def test_reference_cadence_far_from_deadline_is_hourly():
    started = D6 - timedelta(hours=90)
    state = _state(
        [_gw(6, D6)],
        latest={(Job.reference_sync, None): _run(Job.reference_sync, None, started, "succeeded")},
    )
    action = _next_reference(state, started + timedelta(minutes=1))
    assert action.at == started + timedelta(minutes=60)


def test_reference_cadence_entering_window_lands_on_boundary():
    started = D6 - timedelta(hours=48, minutes=30)
    state = _state(
        [_gw(6, D6)],
        latest={(Job.reference_sync, None): _run(Job.reference_sync, None, started, "succeeded")},
    )
    action = _next_reference(state, started)
    assert action.at == D6 - timedelta(hours=48)


def test_reference_cadence_inside_window_is_every_15_min():
    started = D6 - timedelta(hours=40)
    state = _state(
        [_gw(6, D6)],
        latest={(Job.reference_sync, None): _run(Job.reference_sync, None, started, "succeeded")},
    )
    action = _next_reference(state, started)
    assert action.at == started + timedelta(minutes=15)


def test_reference_cadence_failed_run_retries_in_15_min():
    started = D6 - timedelta(hours=90)
    run = _run(Job.reference_sync, None, started, "failed")
    state = _state([_gw(6, D6)], latest={(Job.reference_sync, None): run})
    action = _next_reference(state, started)
    assert action.at == run.finished_at + timedelta(minutes=15)


def test_no_future_deadline_reference_is_every_24h_and_no_snapshot():
    started = D6 - timedelta(hours=90)
    past_gw = _gw(6, started - timedelta(days=1))
    state = _state(
        [past_gw],
        latest={(Job.reference_sync, None): _run(Job.reference_sync, None, started, "succeeded")},
    )
    actions = plan(state, started)
    ref = next(a for a in actions if a.job == Job.reference_sync)
    assert ref.at == started + timedelta(hours=24)
    assert not any(a.job == Job.deadline_snapshot for a in actions)


def test_snapshot_slots_before_t30_plans_t30():
    state = _state([_gw(6, D6)])
    now = D6 - timedelta(hours=1)
    actions = plan(state, now)
    snap = next(a for a in actions if a.job == Job.deadline_snapshot)
    assert snap.at == D6 - timedelta(minutes=30)
    assert snap.gameweek == 6


def test_snapshot_slots_success_at_t30_plans_t5():
    success = _run(Job.deadline_snapshot, 6, D6 - timedelta(minutes=30), "succeeded")
    state = _state([_gw(6, D6)], latest_success={(Job.deadline_snapshot, 6): success})
    actions = plan(state, D6 - timedelta(minutes=20))
    snap = next(a for a in actions if a.job == Job.deadline_snapshot)
    assert snap.at == D6 - timedelta(minutes=5)


def test_snapshot_never_planned_at_or_after_deadline():
    success = _run(Job.deadline_snapshot, 6, D6 - timedelta(minutes=5), "succeeded")
    state = _state([_gw(6, D6)], latest_success={(Job.deadline_snapshot, 6): success})
    actions = plan(state, D6 - timedelta(minutes=1))
    assert not any(a.job == Job.deadline_snapshot for a in actions)


def test_snapshot_retry_failed_at_t30_retries_in_1_min():
    failed = _run(Job.deadline_snapshot, 6, D6 - timedelta(minutes=30), "failed")
    state = _state([_gw(6, D6)], latest={(Job.deadline_snapshot, 6): failed})
    actions = plan(state, D6 - timedelta(minutes=29))
    snap = next(a for a in actions if a.job == Job.deadline_snapshot)
    assert snap.at == failed.finished_at + timedelta(minutes=1)


def test_snapshot_retry_time_at_or_after_deadline_plans_nothing():
    failed = _run(Job.deadline_snapshot, 6, D6 - timedelta(seconds=65), "failed")
    state = _state([_gw(6, D6)], latest={(Job.deadline_snapshot, 6): failed})
    actions = plan(state, D6 - timedelta(seconds=1))
    assert not any(a.job == Job.deadline_snapshot for a in actions)


def test_missed_snapshot_none_before_deadline():
    state = _state([_gw(6, D6)])
    assert missed_snapshot(state, D6 - timedelta(minutes=1)) is None


def test_missed_snapshot_returns_gameweek_after_deadline_with_no_success():
    state = _state([_gw(6, D6)])
    assert missed_snapshot(state, D6 + timedelta(minutes=1)) == 6


def test_missed_snapshot_none_when_t30_succeeded():
    success = _run(Job.deadline_snapshot, 6, D6 - timedelta(minutes=30), "succeeded")
    state = _state([_gw(6, D6)], latest_success={(Job.deadline_snapshot, 6): success})
    assert missed_snapshot(state, D6 + timedelta(minutes=1)) is None


def test_results_then_league_finished_not_checked_plans_nothing():
    state = _state([_gw(6, D6, finished=True, data_checked=False)])
    now = D6 + timedelta(hours=48)
    actions = plan(state, now)
    assert not any(
        a.gameweek == 6 and a.job in (Job.results_sync, Job.league_sync) for a in actions
    )


def test_results_then_league_data_checked_plans_both_results_first():
    state = _state([_gw(6, D6, finished=True, data_checked=True)])
    now = D6 + timedelta(hours=70)
    due = due_actions(state, now)
    gw_jobs = [a for a in due if a.job in (Job.results_sync, Job.league_sync)]
    assert [a.job for a in gw_jobs] == [Job.results_sync, Job.league_sync]
    assert all(a.at == now for a in gw_jobs)


def test_results_then_league_done_plans_nothing():
    now = D6 + timedelta(hours=70)
    results_success = _run(Job.results_sync, 6, now, "succeeded")
    league_success = _run(Job.league_sync, 6, now, "succeeded")
    state = _state(
        [_gw(6, D6, finished=True, data_checked=True)],
        latest_success={
            (Job.results_sync, 6): results_success,
            (Job.league_sync, 6): league_success,
        },
    )
    actions = plan(state, now + timedelta(hours=1))
    assert not any(
        a.gameweek == 6 and a.job in (Job.results_sync, Job.league_sync) for a in actions
    )


def test_results_then_league_failed_retries_after_15_min():
    now = D6 + timedelta(hours=70)
    failed = _run(Job.results_sync, 6, now, "failed")
    state = _state(
        [_gw(6, D6, finished=True, data_checked=True)],
        latest={(Job.results_sync, 6): failed},
    )
    actions = plan(state, now + timedelta(minutes=1))
    results = next(a for a in actions if a.job == Job.results_sync and a.gameweek == 6)
    assert results.at == failed.finished_at + timedelta(minutes=15)


def test_due_actions_priority_snapshot_then_reference_then_gameweek_jobs():
    now = D6 + timedelta(hours=70)
    state = _state([_gw(6, D6, finished=True, data_checked=True), _gw(7, D6 + timedelta(days=7))])
    due = due_actions(state, D6 - timedelta(minutes=30))
    assert due[0].job == Job.deadline_snapshot

    due2 = due_actions(state, now)
    assert [a.job for a in due2] == [Job.reference_sync, Job.results_sync, Job.league_sync]
