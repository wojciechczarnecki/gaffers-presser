import threading
from datetime import UTC, datetime, timedelta

from app.delivery.channels.base import Message

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class FakeChannel:
    def __init__(
        self,
        results: list[str | None | Exception] | None = None,
        name: str = "fake",
        gate: threading.Event | None = None,
        entered: threading.Event | None = None,
    ) -> None:
        self.name = name
        self._results = list(results) if results is not None else ["msg-1"]
        self._gate = gate
        self._entered = entered
        self.calls: list[Message] = []

    def send(self, message: Message) -> str | None:
        self.calls.append(message)
        if self._entered is not None:
            self._entered.set()
        if self._gate is not None:
            assert self._gate.wait(30), "gate never released"
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        pass
