from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from app.fpl.deadlines import next_deadline_after
from app.tweets.store import PollRecord

WINDOW = timedelta(minutes=90)
WINDOW_INTERVAL = timedelta(seconds=20)
SPARSE_INTERVAL = timedelta(minutes=30)
MAX_SLEEP = timedelta(seconds=60)
PRE_SLOT_POLL = timedelta(minutes=10)


def mode(deadlines: list[datetime], t: datetime, window: timedelta = WINDOW) -> str:
    deadline = next_deadline_after(deadlines, t)
    if deadline is not None and deadline - window <= t < deadline:
        return "window"
    return "sparse"


def interval(deadlines: list[datetime], t: datetime, window: timedelta = WINDOW) -> timedelta:
    return WINDOW_INTERVAL if mode(deadlines, t, window) == "window" else SPARSE_INTERVAL


def next_poll_at(
    deadlines: list[datetime],
    last: PollRecord | None,
    now: datetime,
    window: timedelta = WINDOW,
    slot_times: Callable[[datetime], Sequence[datetime]] | None = None,
) -> datetime:
    if last is None:
        return now
    candidate = last.started_at + interval(deadlines, last.started_at, window)
    deadline = next_deadline_after(deadlines, last.started_at)
    if deadline is not None:
        window_start = deadline - window
        if last.started_at < window_start < candidate:
            candidate = window_start
    if slot_times is not None:
        for upcoming in deadlines:
            for slot_at in slot_times(upcoming):
                poll_at = slot_at - PRE_SLOT_POLL
                if (
                    slot_at < upcoming - window
                    and last.started_at < poll_at
                    and candidate >= slot_at
                ):
                    candidate = min(candidate, poll_at)
    if last.outcome == "rate_limited" and last.retry_after_seconds is not None:
        wait = min(timedelta(seconds=last.retry_after_seconds), SPARSE_INTERVAL)
        retry_at = last.finished_at + wait
        candidate = max(candidate, retry_at)
    return candidate
