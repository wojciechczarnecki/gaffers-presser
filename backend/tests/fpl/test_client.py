import httpx
import pytest

from app.fpl.client import USER_AGENT
from app.fpl.errors import FplNotFoundError, FplUnavailableError
from tests.fpl.fakes import FakeFpl


class FakeClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += seconds


def test_user_agent():
    fake = FakeFpl({"bootstrap-static/": {}})
    client = fake.client(sleep=lambda _: None)
    client._get_json("bootstrap-static", "bootstrap-static/")
    assert fake.requests[0].headers["user-agent"] == USER_AGENT


def test_requests_are_throttled():
    clock = FakeClock()
    fake = FakeFpl({"bootstrap-static/": {}, "fixtures/": {}})
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic, min_interval=0.5)

    client._get_json("bootstrap-static", "bootstrap-static/")
    assert clock.sleeps == []

    client._get_json("fixtures", "fixtures/")
    assert clock.sleeps == [0.5]

    clock.time += 1.0
    client._get_json("fixtures", "fixtures/")
    assert clock.sleeps == [0.5]


def _sequence(*responses):
    calls = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(calls)

    return handler


def test_retries_on_429_then_succeeds():
    clock = FakeClock()
    fake = FakeFpl(
        {"bootstrap-static/": _sequence(httpx.Response(429), httpx.Response(200, json={"ok": 1}))}
    )
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic)
    result = client._get_json("bootstrap-static", "bootstrap-static/")
    assert result == {"ok": 1}
    assert clock.sleeps == [1.0]


def test_gives_up_after_five_attempts():
    clock = FakeClock()
    fake = FakeFpl({"bootstrap-static/": lambda request: httpx.Response(503)})
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic)
    with pytest.raises(FplUnavailableError):
        client._get_json("bootstrap-static", "bootstrap-static/")
    assert len(fake.requests) == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]


def test_timeout_retried():
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    fake = FakeFpl({"bootstrap-static/": handler})
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic, max_attempts=2)
    with pytest.raises(FplUnavailableError):
        client._get_json("bootstrap-static", "bootstrap-static/")
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize("status", [200, 503])
def test_game_updating_retried(status):
    clock = FakeClock()
    fake = FakeFpl(
        {
            "bootstrap-static/": _sequence(
                httpx.Response(status, json="The game is being updated."),
                httpx.Response(200, json={"ok": 1}),
            )
        }
    )
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic)
    result = client._get_json("bootstrap-static", "bootstrap-static/")
    assert result == {"ok": 1}


def test_not_found_raises_without_retry():
    clock = FakeClock()
    fake = FakeFpl({"bootstrap-static/": httpx.Response(404)})
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic)
    with pytest.raises(FplNotFoundError):
        client._get_json("bootstrap-static", "bootstrap-static/")
    assert len(fake.requests) == 1
    assert clock.sleeps == []


def test_connection_error_retried():
    clock = FakeClock()
    calls = iter([httpx.ConnectError("reset"), httpx.Response(200, json={"ok": 1})])

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    fake = FakeFpl({"bootstrap-static/": handler})
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic)
    assert client._get_json("bootstrap-static", "bootstrap-static/") == {"ok": 1}
    assert clock.sleeps == [1.0]


def test_non_json_body_retried_then_unavailable():
    clock = FakeClock()
    fake = FakeFpl(
        {"bootstrap-static/": lambda request: httpx.Response(200, text="<html>maintenance</html>")}
    )
    client = fake.client(sleep=clock.sleep, monotonic=clock.monotonic)
    with pytest.raises(FplUnavailableError, match="bootstrap-static"):
        client._get_json("bootstrap-static", "bootstrap-static/")
    assert len(fake.requests) == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]
