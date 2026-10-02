from datetime import UTC, datetime, timedelta

from app.alerts.config import AlertConfig
from app.alerts.schemas import AlertDeadline
from app.core.errors import ConfigError
from app.fpl.deadlines import next_deadline_after
from app.worker.schedule import GameweekState

POLLING_MARGIN = timedelta(minutes=10)
MIN_POLLING_WINDOW = timedelta(minutes=90)
BREAKING_WAKE_SECONDS = 5.0
MAX_WAKE_SECONDS = 60.0
MIN_WAKE_SECONDS = 1.0


def deadline_key(season: str, gameweek: int) -> str:
    return f"{season}:gw{gameweek}"


def rehearsal_key(deadline_at: datetime) -> str:
    return f"rehearsal:{deadline_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%MZ')}"


def alert_deadlines(
    season: str | None, gameweeks: list[GameweekState], rehearsal: datetime | None
) -> list[AlertDeadline]:
    deadlines = []
    if season is not None:
        deadlines = [
            AlertDeadline(deadline_key(season, g.fpl_id), g.deadline_at, False, season, g.fpl_id)
            for g in gameweeks
        ]
    if rehearsal is not None:
        deadlines.append(AlertDeadline(rehearsal_key(rehearsal), rehearsal, True, None, None))
    return sorted(deadlines, key=lambda d: d.deadline_at)


def next_alert_deadline(deadlines: list[AlertDeadline], now: datetime) -> AlertDeadline | None:
    return next_deadline_after(deadlines, now, key=lambda d: d.deadline_at)


def due_slots(
    deadline: AlertDeadline, slots: tuple[int, ...], done: set[int], now: datetime
) -> list[int]:
    if now >= deadline.deadline_at:
        return []
    return [
        slot
        for slot in slots
        if slot not in done and now >= deadline.deadline_at - timedelta(minutes=slot)
    ]


def breaking_open(deadline: AlertDeadline, last_slot_done: bool, now: datetime) -> bool:
    return last_slot_done and now < deadline.deadline_at


def next_wake(
    deadline: AlertDeadline | None, slots: tuple[int, ...], done: set[int], now: datetime
) -> float:
    if deadline is None or now >= deadline.deadline_at:
        return MAX_WAKE_SECONDS
    if breaking_open(deadline, slots[-1] in done, now):
        return BREAKING_WAKE_SECONDS
    upcoming = [
        deadline.deadline_at - timedelta(minutes=slot) for slot in slots if slot not in done
    ]
    wait = (min(upcoming) - now).total_seconds()
    return min(max(wait, MIN_WAKE_SECONDS), MAX_WAKE_SECONDS)


def polling_window(config: AlertConfig | None) -> timedelta:
    if config is None:
        return MIN_POLLING_WINDOW
    return max(MIN_POLLING_WINDOW, timedelta(minutes=config.slots[0]) + POLLING_MARGIN)


def check_rehearsal(config: AlertConfig, real_deadlines: list[datetime], now: datetime) -> None:
    rehearsal = config.rehearsal_deadline
    if rehearsal is None or rehearsal <= now:
        return
    window = timedelta(minutes=config.slots[0]) + POLLING_MARGIN
    for deadline in real_deadlines:
        if rehearsal - window < deadline and deadline - window < rehearsal:
            raise ConfigError(
                "ALERT_REHEARSAL_DEADLINE overlaps the alert window of a real deadline"
            )
