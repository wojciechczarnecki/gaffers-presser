from datetime import UTC, datetime, timedelta

from app.tweets.schedule import interval, mode, next_poll_at
from app.tweets.store import PollRecord

DEADLINE = datetime(2026, 10, 10, 18, 30, 0, tzinfo=UTC)


def _poll(
    started_at: datetime,
    outcome: str = "succeeded",
    retry_after_seconds: float | None = None,
    finished_at: datetime | None = None,
) -> PollRecord:
    return PollRecord(
        source="twscrape",
        started_at=started_at,
        finished_at=finished_at if finished_at is not None else started_at,
        outcome=outcome,
        new_posts=0,
        retry_after_seconds=retry_after_seconds,
    )


def test_interval_at_window_boundaries():
    deadlines = [DEADLINE]
    assert mode(deadlines, DEADLINE - timedelta(minutes=90, seconds=1)) == "sparse"
    assert mode(deadlines, DEADLINE - timedelta(minutes=90)) == "window"
    assert mode(deadlines, DEADLINE - timedelta(seconds=1)) == "window"
    assert mode(deadlines, DEADLINE) == "sparse"
    assert mode([], DEADLINE) == "sparse"


def test_next_poll_at_window_interval():
    last = _poll(DEADLINE - timedelta(minutes=45))
    assert next_poll_at([DEADLINE], last, DEADLINE - timedelta(minutes=45)) == DEADLINE - timedelta(
        minutes=45
    ) + timedelta(seconds=20)


def test_next_poll_at_sparse_interval():
    last = _poll(DEADLINE - timedelta(days=1))
    assert next_poll_at([DEADLINE], last, DEADLINE - timedelta(days=1)) == DEADLINE - timedelta(
        days=1
    ) + timedelta(minutes=30)


def test_sparse_poll_does_not_skip_window_start():
    last = _poll(DEADLINE - timedelta(minutes=100))
    assert next_poll_at(
        [DEADLINE], last, DEADLINE - timedelta(minutes=100)
    ) == DEADLINE - timedelta(minutes=90)


def test_rate_limit_delays_next_poll_beyond_interval():
    started = DEADLINE - timedelta(minutes=45)
    last = _poll(started, outcome="rate_limited", retry_after_seconds=120)
    assert next_poll_at([DEADLINE], last, started) == started + timedelta(seconds=120)


def test_rate_limit_shorter_than_interval_keeps_interval():
    started = DEADLINE - timedelta(minutes=45)
    last = _poll(started, outcome="rate_limited", retry_after_seconds=5)
    assert next_poll_at([DEADLINE], last, started) == started + timedelta(seconds=20)


def test_rate_limit_wait_is_capped_at_the_sparse_interval():
    started = DEADLINE - timedelta(minutes=45)
    last = _poll(started, outcome="rate_limited", retry_after_seconds=86_400)
    assert next_poll_at([DEADLINE], last, started) == started + timedelta(minutes=30)


def test_no_last_poll_is_now():
    now = DEADLINE - timedelta(hours=2)
    assert next_poll_at([DEADLINE], None, now) == now


def test_no_future_deadline_is_sparse():
    started = DEADLINE + timedelta(days=1)
    last = _poll(started)
    assert next_poll_at([DEADLINE], last, started) == started + timedelta(minutes=30)


def test_custom_window_and_extra_deadline():
    window = timedelta(minutes=130)
    deadlines = [DEADLINE]
    assert mode(deadlines, DEADLINE - timedelta(minutes=130), window) == "window"
    assert mode(deadlines, DEADLINE - timedelta(minutes=130, seconds=1), window) == "sparse"
    assert mode(deadlines, DEADLINE - timedelta(minutes=100)) == "sparse"
    assert mode(deadlines, DEADLINE - timedelta(minutes=100), window) == "window"

    last = _poll(DEADLINE - timedelta(minutes=140))
    assert next_poll_at(
        deadlines, last, DEADLINE - timedelta(minutes=140), window
    ) == DEADLINE - timedelta(minutes=130)

    extra = DEADLINE + timedelta(days=3)
    assert mode([DEADLINE, extra], extra - timedelta(minutes=125), window) == "window"
    assert interval([DEADLINE, extra], extra - timedelta(minutes=125), window) == timedelta(
        seconds=20
    )
