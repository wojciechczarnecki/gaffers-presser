from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Job(StrEnum):
    reference_sync = "reference_sync"
    deadline_snapshot = "deadline_snapshot"
    results_sync = "results_sync"
    league_sync = "league_sync"


REFERENCE_INTERVAL = timedelta(minutes=60)
REFERENCE_INTERVAL_NEAR_DEADLINE = timedelta(minutes=15)
REFERENCE_WINDOW_BEFORE_DEADLINE = timedelta(hours=48)
REFERENCE_INTERVAL_NO_DEADLINE = timedelta(hours=24)
REFERENCE_RETRY = timedelta(minutes=15)
SNAPSHOT_SLOT_T30 = timedelta(minutes=30)
SNAPSHOT_SLOT_T5 = timedelta(minutes=5)
SNAPSHOT_RETRY = timedelta(minutes=1)
GAMEWEEK_JOB_RETRY = timedelta(minutes=15)


@dataclass(frozen=True)
class GameweekState:
    fpl_id: int
    deadline_at: datetime
    finished: bool
    data_checked: bool


@dataclass(frozen=True)
class RunRecord:
    job: Job
    gameweek: int | None
    started_at: datetime
    finished_at: datetime
    outcome: str  # succeeded | failed


@dataclass(frozen=True)
class ScheduleState:
    season: str | None
    gameweeks: list[GameweekState]
    latest: dict[tuple[Job, int | None], RunRecord]
    latest_success: dict[tuple[Job, int | None], RunRecord]


@dataclass(frozen=True)
class PlannedAction:
    at: datetime
    job: Job
    gameweek: int | None
    season: str | None


def _deadline_after(gameweeks: list[GameweekState], t: datetime) -> datetime | None:
    deadlines = [gw.deadline_at for gw in gameweeks if gw.deadline_at > t]
    return min(deadlines) if deadlines else None


def _gameweek_with_deadline_after(
    gameweeks: list[GameweekState], t: datetime
) -> GameweekState | None:
    candidates = [gw for gw in gameweeks if gw.deadline_at > t]
    return min(candidates, key=lambda g: g.deadline_at) if candidates else None


def _gameweek_with_greatest_deadline_at_or_before(
    gameweeks: list[GameweekState], t: datetime
) -> GameweekState | None:
    candidates = [gw for gw in gameweeks if gw.deadline_at <= t]
    return max(candidates, key=lambda g: g.deadline_at) if candidates else None


def _next_reference_sync(state: ScheduleState, now: datetime) -> PlannedAction:
    last = state.latest.get((Job.reference_sync, None))
    if last is None:
        at = now
    else:
        deadline = _deadline_after(state.gameweeks, last.started_at)
        if deadline is None:
            at = last.started_at + REFERENCE_INTERVAL_NO_DEADLINE
        else:
            at = min(
                last.started_at + REFERENCE_INTERVAL,
                max(
                    last.started_at + REFERENCE_INTERVAL_NEAR_DEADLINE,
                    deadline - REFERENCE_WINDOW_BEFORE_DEADLINE,
                ),
            )
        if last.outcome == "failed":
            at = min(at, last.finished_at + REFERENCE_RETRY)
    return PlannedAction(at=at, job=Job.reference_sync, gameweek=None, season=state.season)


def _next_deadline_snapshot(state: ScheduleState, now: datetime) -> PlannedAction | None:
    gw = _gameweek_with_deadline_after(state.gameweeks, now)
    if gw is None:
        return None
    deadline = gw.deadline_at
    slots = [deadline - SNAPSHOT_SLOT_T30, deadline - SNAPSHOT_SLOT_T5]

    candidate = None
    for slot in slots:
        success = state.latest_success.get((Job.deadline_snapshot, gw.fpl_id))
        if success is not None and success.started_at >= slot:
            continue
        candidate = slot
        break
    if candidate is None:
        return None

    last = state.latest.get((Job.deadline_snapshot, gw.fpl_id))
    if (
        last is not None
        and last.outcome == "failed"
        and last.started_at >= deadline - SNAPSHOT_SLOT_T30
    ):
        candidate = max(candidate, last.finished_at + SNAPSHOT_RETRY)

    if candidate >= deadline:
        return None
    return PlannedAction(
        at=candidate, job=Job.deadline_snapshot, gameweek=gw.fpl_id, season=state.season
    )


def missed_snapshot(state: ScheduleState, now: datetime) -> int | None:
    gw = _gameweek_with_greatest_deadline_at_or_before(state.gameweeks, now)
    if gw is None:
        return None
    success = state.latest_success.get((Job.deadline_snapshot, gw.fpl_id))
    if success is not None and success.started_at >= gw.deadline_at - SNAPSHOT_SLOT_T30:
        return None
    return gw.fpl_id


def _next_gameweek_jobs(state: ScheduleState, now: datetime) -> list[PlannedAction]:
    actions = []
    for gw in state.gameweeks:
        if not (gw.finished and gw.data_checked):
            continue
        for job in (Job.results_sync, Job.league_sync):
            if state.latest_success.get((job, gw.fpl_id)) is not None:
                continue
            last = state.latest.get((job, gw.fpl_id))
            if last is not None and last.outcome == "failed":
                at = last.finished_at + GAMEWEEK_JOB_RETRY
            else:
                at = now
            actions.append(PlannedAction(at=at, job=job, gameweek=gw.fpl_id, season=state.season))
    return actions


def plan(state: ScheduleState, now: datetime) -> list[PlannedAction]:
    actions = [_next_reference_sync(state, now)]
    snapshot = _next_deadline_snapshot(state, now)
    if snapshot is not None:
        actions.append(snapshot)
    actions.extend(_next_gameweek_jobs(state, now))
    return sorted(actions, key=lambda a: a.at)


def _priority_key(action: PlannedAction) -> tuple[int, int, int]:
    if action.job == Job.deadline_snapshot:
        return (0, 0, 0)
    if action.job == Job.reference_sync:
        return (1, 0, 0)
    sub = 0 if action.job == Job.results_sync else 1
    return (2, action.gameweek or 0, sub)


def due_actions(state: ScheduleState, now: datetime) -> list[PlannedAction]:
    due = [a for a in plan(state, now) if a.at <= now]
    return sorted(due, key=_priority_key)
