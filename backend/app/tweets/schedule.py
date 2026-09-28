from datetime import datetime, timedelta

from app.tweets.store import PollRecord

WINDOW = timedelta(minutes=90)
WINDOW_INTERVAL = timedelta(seconds=20)
SPARSE_INTERVAL = timedelta(minutes=30)
MAX_SLEEP = timedelta(seconds=60)


def next_deadline_after(deadlines: list[datetime], t: datetime) -> datetime | None:
    candidates = [deadline for deadline in deadlines if deadline > t]
    return min(candidates) if candidates else None


def mode(deadlines: list[datetime], t: datetime) -> str:
    deadline = next_deadline_after(deadlines, t)
    if deadline is not None and deadline - WINDOW <= t < deadline:
        return "window"
    return "sparse"


def interval(deadlines: list[datetime], t: datetime) -> timedelta:
    return WINDOW_INTERVAL if mode(deadlines, t) == "window" else SPARSE_INTERVAL


def next_poll_at(deadlines: list[datetime], last: PollRecord | None, now: datetime) -> datetime:
    if last is None:
        return now
    candidate = last.started_at + interval(deadlines, last.started_at)
    deadline = next_deadline_after(deadlines, last.started_at)
    if deadline is not None:
        window_start = deadline - WINDOW
        if last.started_at < window_start < candidate:
            candidate = window_start
    if last.outcome == "rate_limited" and last.retry_after_seconds is not None:
        wait = min(timedelta(seconds=last.retry_after_seconds), SPARSE_INTERVAL)
        retry_at = last.finished_at + wait
        candidate = max(candidate, retry_at)
    return candidate
