from datetime import UTC, datetime, timedelta

from app.fpl.calendar import GameweekState
from app.worker.schedule import (
    Job,
    PlannedAction,
    RunRecord,
    ScheduleState,
    due_actions,
    missed_snapshot,
    outlook,
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


def _gameweek_jobs(actions):
    return [(a.job, a.gameweek) for a in actions if a.job in (Job.results_sync, Job.league_sync)]


def test_results_then_league_data_checked_plans_results_only():
    state = _state([_gw(6, D6, finished=True, data_checked=True)])
    now = D6 + timedelta(hours=70)
    due = due_actions(state, now)
    assert _gameweek_jobs(due) == [(Job.results_sync, 6)]
    assert _gameweek_jobs(plan(state, now)) == [(Job.results_sync, 6)]


def test_league_not_planned_while_results_failed():
    now = D6 + timedelta(hours=70)
    failed = _run(Job.results_sync, 6, now, "failed")
    state = _state(
        [_gw(6, D6, finished=True, data_checked=True)],
        latest={(Job.results_sync, 6): failed},
    )
    for later in (timedelta(minutes=1), timedelta(minutes=20)):
        assert _gameweek_jobs(plan(state, now + later)) == [(Job.results_sync, 6)]


def test_league_planned_despite_failed_rerun_after_success():
    # latest_success counts, not latest: a failed re-run after a success does not block
    now = D6 + timedelta(hours=70)
    success = _run(Job.results_sync, 6, now, "succeeded")
    state = _state(
        [_gw(6, D6, finished=True, data_checked=True)],
        latest={(Job.results_sync, 6): _run(Job.results_sync, 6, now, "failed")},
        latest_success={(Job.results_sync, 6): success},
    )
    assert _gameweek_jobs(due_actions(state, now + timedelta(minutes=1))) == [(Job.league_sync, 6)]


def test_league_planned_now_once_results_succeeded():
    now = D6 + timedelta(hours=70)
    success = _run(Job.results_sync, 6, now, "succeeded")
    state = _state(
        [_gw(6, D6, finished=True, data_checked=True)],
        latest={(Job.results_sync, 6): success},
        latest_success={(Job.results_sync, 6): success},
    )
    later = now + timedelta(seconds=10)
    due = due_actions(state, later)
    assert _gameweek_jobs(due) == [(Job.league_sync, 6)]
    assert next(a for a in due if a.job == Job.league_sync).at == later


def test_league_failed_retries_after_15_min_once_results_succeeded():
    now = D6 + timedelta(hours=70)
    success = _run(Job.results_sync, 6, now, "succeeded")
    failed = _run(Job.league_sync, 6, now + timedelta(minutes=1), "failed")
    state = _state(
        [_gw(6, D6, finished=True, data_checked=True)],
        latest={(Job.results_sync, 6): success, (Job.league_sync, 6): failed},
        latest_success={(Job.results_sync, 6): success},
    )
    league = next(a for a in plan(state, now + timedelta(minutes=2)) if a.job == Job.league_sync)
    assert league.at == failed.finished_at + timedelta(minutes=15)


def test_catch_up_over_several_gameweeks_keeps_results_before_league():
    gameweeks = [
        _gw(4, D5 - timedelta(days=14), finished=True, data_checked=True),
        _gw(5, D5, finished=True, data_checked=True),
        _gw(6, D6, finished=True, data_checked=True),
    ]
    now = D6 + timedelta(hours=70)
    latest, latest_success = {}, {}
    failing = {(Job.results_sync, 5)}  # fails once, as FPL does while it updates
    order = []
    for _ in range(20):
        due = _gameweek_jobs(due_actions(_state(gameweeks, latest, latest_success), now))
        for job, gw in due:
            if job == Job.league_sync:
                assert (Job.results_sync, gw) in latest_success
        if not due:
            if len(order) == 6:
                break
            now += timedelta(minutes=15)
            continue
        key = due[0]
        outcome = "failed" if key in failing else "succeeded"
        failing.discard(key)
        run = _run(key[0], key[1], now, outcome)
        latest[key] = run
        if outcome == "succeeded":
            latest_success[key] = run
            order.append(key)
        now += timedelta(minutes=1)
    assert order == [
        (Job.results_sync, 4),
        (Job.league_sync, 4),
        (Job.results_sync, 6),  # gameweek 5 waits for its retry; 6 does not wait for it
        (Job.league_sync, 6),
        (Job.results_sync, 5),
        (Job.league_sync, 5),
    ]


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
    assert [a.job for a in due2] == [Job.reference_sync, Job.results_sync]

    success = _run(Job.results_sync, 6, now, "succeeded")
    state3 = _state(
        state.gameweeks,
        latest={(Job.results_sync, 6): success},
        latest_success={(Job.results_sync, 6): success},
    )
    due3 = due_actions(state3, now + timedelta(seconds=10))
    assert [a.job for a in due3] == [Job.reference_sync, Job.league_sync]


def _snapshot_times(actions):
    return [a.at for a in actions if a.job == Job.deadline_snapshot]


def test_outlook_lists_both_remaining_snapshot_slots():
    state = _state([_gw(6, D6)])
    now = D6 - timedelta(hours=1)
    assert _snapshot_times(outlook(state, now)) == [
        D6 - timedelta(minutes=30),
        D6 - timedelta(minutes=5),
    ]
    assert _snapshot_times(plan(state, now)) == [D6 - timedelta(minutes=30)]


def test_outlook_lists_only_t5_after_t30_succeeded():
    success = _run(Job.deadline_snapshot, 6, D6 - timedelta(minutes=30), "succeeded")
    state = _state([_gw(6, D6)], latest_success={(Job.deadline_snapshot, 6): success})
    assert _snapshot_times(outlook(state, D6 - timedelta(minutes=20))) == [
        D6 - timedelta(minutes=5)
    ]


def test_outlook_keeps_t5_behind_a_t30_retry():
    failed = _run(Job.deadline_snapshot, 6, D6 - timedelta(minutes=30), "failed")
    state = _state([_gw(6, D6)], latest={(Job.deadline_snapshot, 6): failed})
    assert _snapshot_times(outlook(state, D6 - timedelta(minutes=29))) == [
        failed.finished_at + timedelta(minutes=1),
        D6 - timedelta(minutes=5),
    ]


def test_outlook_without_future_deadline_equals_plan():
    started = D6 - timedelta(hours=90)
    state = _state(
        [_gw(6, started - timedelta(days=1))],
        latest={(Job.reference_sync, None): _run(Job.reference_sync, None, started, "succeeded")},
    )
    assert outlook(state, started) == plan(state, started)
