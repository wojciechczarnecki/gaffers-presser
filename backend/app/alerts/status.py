from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from app.alerts.config import AlertConfig
from app.alerts.schedule import alert_deadlines, next_alert_deadline, resolve_slots
from app.alerts.schemas import AlertDeadline
from app.alerts.store import AlertRow, done_slots, status_rows
from app.worker.store import load_state


@dataclass(frozen=True)
class AlertStatus:
    deadline: AlertDeadline | None
    next_slot_kind: str | None
    next_slot_at: datetime | None
    breaking_until: datetime | None
    last: AlertRow | None
    failed: int


def alert_status(engine: Engine, config: AlertConfig, now: datetime) -> AlertStatus:
    state = load_state(engine)
    deadlines = alert_deadlines(state.season, state.gameweeks, config.rehearsal_deadline)
    deadline = next_alert_deadline(deadlines, now)
    if deadline is None:
        return AlertStatus(None, None, None, None, None, 0)
    with Session(engine) as session:
        done = done_slots(session, deadline.key)
        last, failed = status_rows(session, deadline.key)
    pending = [
        (index, slot)
        for index, slot in enumerate(resolve_slots(config.slots, deadline.deadline_at).minutes)
        if slot not in done
    ]
    if pending:
        index, slot = pending[0]
        return AlertStatus(
            deadline,
            "digest" if index == 0 else "news",
            deadline.deadline_at - timedelta(minutes=slot),
            None,
            last,
            failed,
        )
    return AlertStatus(deadline, None, None, deadline.deadline_at, last, failed)


def status_line(status: AlertStatus, fmt: Callable[[datetime], str]) -> str:
    if status.deadline is None:
        head = "no upcoming deadline"
    elif status.next_slot_at is not None:
        head = f"next slot: {status.next_slot_kind} {fmt(status.next_slot_at)}"
    else:
        head = f"breaking until {fmt(status.breaking_until)}"
    if status.last is None:
        last = "never"
    else:
        last = f"{status.last.kind} {fmt(status.last.recorded_at)} {status.last.status}"
    return f"Alerts: {head}  last alert: {last}  failed: {status.failed}"
