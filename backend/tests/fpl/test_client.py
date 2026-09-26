from app.fpl.client import USER_AGENT
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
