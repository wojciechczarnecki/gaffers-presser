import threading

from app.core.retry import with_retries


class Clock:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_success_on_second_attempt():
    clock = Clock()
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 2:
            raise ValueError("boom")
        return "ok"

    outcome = with_retries(fn, clock, threading.Event())
    assert outcome.result == "ok"
    assert outcome.attempts == 2
    assert outcome.error is None
    assert clock.sleeps == [2.0]


def test_exhaustion_keeps_last_error():
    clock = Clock()
    errors = [ValueError("a"), KeyError("b"), RuntimeError("c")]

    def fn():
        raise errors.pop(0)

    outcome = with_retries(fn, clock, threading.Event())
    assert outcome.result is None
    assert outcome.attempts == 3
    assert isinstance(outcome.error, RuntimeError)
    assert clock.sleeps == [2.0, 4.0]
    assert not outcome.stopped


def test_stop_before_first_attempt():
    stop = threading.Event()
    stop.set()
    called = []
    outcome = with_retries(lambda: called.append(1), Clock(), stop)
    assert outcome.stopped
    assert outcome.attempts == 0
    assert called == []


def test_stop_set_during_backoff():
    stop = threading.Event()

    class StoppingClock(Clock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            stop.set()

    def fn():
        raise ValueError("boom")

    clock = StoppingClock()
    outcome = with_retries(fn, clock, stop)
    assert outcome.stopped
    assert outcome.attempts == 1
    assert isinstance(outcome.error, ValueError)
    assert clock.sleeps == [2.0]


def test_stop_set_by_the_failing_call_skips_the_sleep():
    stop = threading.Event()

    def fn():
        stop.set()
        raise ValueError("boom")

    clock = Clock()
    outcome = with_retries(fn, clock, stop)
    assert outcome.stopped
    assert clock.sleeps == []


def test_backoff_index_is_clamped():
    clock = Clock()

    def fn():
        raise ValueError("boom")

    outcome = with_retries(fn, clock, threading.Event(), attempts=5, backoff=(1.0, 3.0))
    assert outcome.attempts == 5
    assert clock.sleeps == [1.0, 3.0, 3.0, 3.0]


def test_non_retryable_error_stops_at_once():
    clock = Clock()
    calls = []

    def fn():
        calls.append(1)
        raise KeyError("fatal")

    outcome = with_retries(
        fn, clock, threading.Event(), retryable=lambda exc: not isinstance(exc, KeyError)
    )
    assert outcome.attempts == 1
    assert calls == [1]
    assert isinstance(outcome.error, KeyError)
    assert clock.sleeps == []
    assert not outcome.stopped


def test_wait_hook_overrides_the_delay():
    clock = Clock()

    def fn():
        raise ValueError("boom")

    outcome = with_retries(fn, clock, threading.Event(), wait=lambda exc, default: default * 10 + 1)
    assert outcome.attempts == 3
    assert clock.sleeps == [21.0, 41.0]
