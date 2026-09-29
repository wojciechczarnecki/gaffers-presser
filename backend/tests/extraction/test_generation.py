import httpx
from pydantic import SecretStr

from app.extraction.generation import make_host_lookup


def _lookup(statuses, host="DeepInfra", attempts=3):
    calls = []
    sleeps = []
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["id"])
        seen_headers.append(request.headers["authorization"])
        status = statuses[min(len(calls), len(statuses)) - 1]
        body = {"data": {"provider_name": host}} if status == 200 else {}
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    lookup = make_host_lookup(
        SecretStr("sk-test"), client=client, sleep=sleeps.append, attempts=attempts, delay_seconds=3
    )
    return lookup, calls, sleeps, seen_headers


def test_returns_the_provider_name():
    lookup, calls, sleeps, headers = _lookup([200])
    assert lookup("gen-1") == "DeepInfra"
    assert calls == ["gen-1"]
    assert sleeps == []
    assert headers == ["Bearer sk-test"]


def test_retries_while_the_generation_is_not_there_yet():
    lookup, calls, sleeps, _ = _lookup([404, 404, 200])
    assert lookup("gen-1") == "DeepInfra"
    assert len(calls) == 3
    assert sleeps == [3, 3]


def test_gives_up_after_the_attempts():
    lookup, calls, sleeps, _ = _lookup([404])
    assert lookup("gen-1") is None
    assert len(calls) == 3
    assert sleeps == [3, 3]


def test_other_errors_are_not_retried():
    lookup, calls, _, _ = _lookup([500])
    assert lookup("gen-1") is None
    assert len(calls) == 1
