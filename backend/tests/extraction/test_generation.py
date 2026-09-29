import httpx
import pytest
from pydantic import SecretStr

from app.extraction.generation import HostLookup


def _lookup(statuses, host="DeepInfra", attempts=3, body=None, error=None, **kwargs):
    calls = []
    sleeps = []
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["id"])
        seen_headers.append(request.headers["authorization"])
        if error is not None:
            raise error
        status = statuses[min(len(calls), len(statuses)) - 1]
        if body is not None:
            return httpx.Response(status, content=body)
        return httpx.Response(
            status, json={"data": {"provider_name": host}} if status == 200 else {}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    lookup = HostLookup(
        SecretStr("sk-test"),
        client=client,
        sleep=sleeps.append,
        attempts=attempts,
        delay_seconds=3,
        **kwargs,
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


@pytest.mark.parametrize(
    "body",
    [b"<html>not json</html>", b"[]", b'{"data": null}', b'{"data": []}', b"{}", b'{"data": {}}'],
    ids=["not-json", "list", "null-data", "list-data", "no-data", "no-provider-name"],
)
def test_a_200_without_a_usable_record_gives_none(body):
    lookup, calls, sleeps, _ = _lookup([200], body=body)
    assert lookup("gen-1") is None
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout("timed out"), httpx.ConnectError("refused")],
    ids=["connect-timeout", "connect-error"],
)
def test_network_errors_give_none_without_retry(error):
    lookup, calls, sleeps, _ = _lookup([200], error=error)
    assert lookup("gen-1") is None
    assert len(calls) == 1
    assert sleeps == []


def test_waiting_is_capped_across_lookups():
    lookup, calls, sleeps, _ = _lookup([404], attempts=8, max_total_wait_seconds=10)
    assert [lookup(f"gen-{n}") for n in range(3)] == [None, None, None]
    assert sleeps == [3, 3, 3]
    assert len(calls) == 4 + 1 + 1


def test_close_closes_only_an_own_client():
    injected = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    HostLookup(SecretStr("sk-test"), client=injected).close()
    assert not injected.is_closed

    own = HostLookup(SecretStr("sk-test"))
    own.close()
    assert own._http.is_closed
