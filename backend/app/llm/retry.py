import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.core.clock import Clock

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryOutcome(Generic[T]):
    result: T | None
    attempts: int
    error: Exception | None
    stopped: bool = False


def with_retries(
    fn: Callable[[], T],
    clock: Clock,
    stop_event: threading.Event,
    *,
    attempts: int = MAX_ATTEMPTS,
    backoff: Sequence[float] = RETRY_BACKOFF_SECONDS,
    what: str = "call",
) -> RetryOutcome[T]:
    made = 0
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        if stop_event.is_set():
            return RetryOutcome(None, made, last_exc, stopped=True)
        made = attempt
        try:
            return RetryOutcome(fn(), made, None)
        except Exception as exc:
            last_exc = exc
            logger.warning("%s attempt failed: %s", what, type(exc).__name__)
            if attempt >= attempts:
                break
            if stop_event.is_set():
                return RetryOutcome(None, made, last_exc, stopped=True)
            clock.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
            if stop_event.is_set():
                return RetryOutcome(None, made, last_exc, stopped=True)
    return RetryOutcome(None, made, last_exc)
