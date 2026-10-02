import json

import httpx
import pytest

from app.delivery.channels.base import (
    ChannelPayloadError,
    ChannelRateLimitedError,
    ChannelRejectedError,
    ChannelUnavailableError,
    Message,
)
from app.delivery.channels.resend import ResendChannel
from tests.delivery.payloads import load

API_KEY = "re_test_key_synthetic"
SENDER = "presser@example.test"
RECIPIENT = "owner@example.test"
MESSAGE = Message(title="Tytul testowy", text="Tresc testowa", html="<p>Tresc testowa</p>")
KEY = "alert:gw7:fpl123:t-30"


def channel_for(handler) -> ResendChannel:
    return ResendChannel(API_KEY, SENDER, RECIPIENT, transport=httpx.MockTransport(handler))


def respond(status: int, payload: str, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=load(payload), headers=headers)

    return handler


def test_sends_one_post_and_returns_the_id():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=load("resend-200"))

    assert channel_for(handler).send(MESSAGE, KEY) == load("resend-200")["id"]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/emails"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Idempotency-Key"] == KEY
    assert json.loads(request.content) == {
        "from": SENDER,
        "to": [RECIPIENT],
        "subject": MESSAGE.title,
        "text": MESSAGE.text,
        "html": MESSAGE.html,
    }


def test_html_is_omitted_when_absent():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=load("resend-200"))

    channel_for(handler).send(Message(title="T", text="B"), KEY)
    assert "html" not in bodies[0]


def test_testing_domain_recipient_is_rejected():
    with pytest.raises(ChannelRejectedError) as info:
        channel_for(respond(403, "resend-403-testing-domain")).send(MESSAGE, KEY)
    assert info.value.http_status == 403


def test_unprocessable_message_is_rejected():
    with pytest.raises(ChannelRejectedError) as info:
        channel_for(respond(422, "resend-422")).send(MESSAGE, KEY)
    assert info.value.http_status == 422


def test_rate_limit_carries_retry_after():
    handler = respond(429, "resend-429", {"Retry-After": "3"})
    with pytest.raises(ChannelRateLimitedError) as info:
        channel_for(handler).send(MESSAGE, KEY)
    assert info.value.retry_after == 3.0
    assert info.value.http_status == 429


def test_rate_limit_without_header_has_no_retry_after():
    with pytest.raises(ChannelRateLimitedError) as info:
        channel_for(respond(429, "resend-429")).send(MESSAGE, KEY)
    assert info.value.retry_after is None


def test_server_error_is_unavailable():
    with pytest.raises(ChannelUnavailableError) as info:
        channel_for(respond(500, "resend-500")).send(MESSAGE, KEY)
    assert info.value.http_status == 500


def test_timeout_is_unavailable_without_status():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ChannelUnavailableError) as info:
        channel_for(handler).send(MESSAGE, KEY)
    assert info.value.http_status is None


def test_connection_error_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ChannelUnavailableError):
        channel_for(handler).send(MESSAGE, KEY)


@pytest.mark.parametrize("body", [{}, {"id": ""}, {"id": 7}, []])
def test_success_without_an_id_is_a_payload_error(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(ChannelPayloadError):
        channel_for(handler).send(MESSAGE, KEY)


def test_non_json_success_is_a_payload_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    with pytest.raises(ChannelPayloadError):
        channel_for(handler).send(MESSAGE, KEY)


def test_every_attempt_carries_the_same_idempotency_key():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("reply lost", request=request)
        return httpx.Response(200, json=load("resend-200"))

    channel = channel_for(handler)
    with pytest.raises(ChannelUnavailableError):
        channel.send(MESSAGE, KEY)
    assert channel.send(MESSAGE, KEY) == load("resend-200")["id"]
    assert [request.headers["Idempotency-Key"] for request in requests] == [KEY, KEY]
    assert json.loads(requests[0].content) == json.loads(requests[1].content)
