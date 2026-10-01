import logging
import threading
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.delivery import service as service_module
from app.delivery.channels.base import (
    ChannelRateLimitedError,
    ChannelRejectedError,
    ChannelUnavailableError,
    InvalidMessageError,
    Message,
)
from app.delivery.channels.resend import ResendChannel
from app.delivery.models import DeliveryLog
from app.delivery.service import DeliveryService
from tests.delivery.fakes import START, FakeChannel, FixedClock

MESSAGE = Message(title="Tytul", text="Tresc", html="<p>Tresc</p>")


@pytest.fixture(autouse=True)
def _reset_disabled_notice():
    service_module._disabled_noticed = False
    yield
    service_module._disabled_noticed = False


def rows(engine) -> list[DeliveryLog]:
    with Session(engine) as session:
        return list(session.exec(select(DeliveryLog).order_by(DeliveryLog.id)).all())


def make_service(db, channel, clock=None):
    return DeliveryService(db, channel, clock or FixedClock())


def test_invalid_message_writes_no_row_and_calls_nothing(db):
    channel = FakeChannel()
    service = make_service(db, channel)
    with pytest.raises(InvalidMessageError):
        service.send("alert:gw1:k", "alert", Message(title="", text="Tresc"))
    assert channel.calls == []
    assert rows(db) == []


def test_key_and_kind_are_required_and_stored(db):
    channel = FakeChannel()
    service = make_service(db, channel)
    with pytest.raises(ValueError):
        service.send("", "alert", MESSAGE)
    with pytest.raises(ValueError):
        service.send("alert:gw1:k", "other", MESSAGE)  # type: ignore[arg-type]
    assert rows(db) == []
    assert channel.calls == []

    for index, kind in enumerate(("alert", "presser", "test")):
        service.send(f"{kind}:{index}", kind, MESSAGE)  # type: ignore[arg-type]
    assert [(row.idempotency_key, row.kind) for row in rows(db)] == [
        ("alert:0", "alert"),
        ("presser:1", "presser"),
        ("test:2", "test"),
    ]


def test_first_send_writes_full_row(db):
    clock = FixedClock()
    channel = FakeChannel(["provider-42"])
    service = make_service(db, channel, clock)

    outcome = service.send("presser:gw7:league-a", "presser", MESSAGE)

    (row,) = rows(db)
    assert row.idempotency_key == "presser:gw7:league-a"
    assert row.kind == "presser"
    assert row.channel == "fake"
    assert row.title == MESSAGE.title
    assert row.text_body == MESSAGE.text
    assert row.html_body == MESSAGE.html
    assert row.status == "sent"
    assert row.provider_message_id == "provider-42"
    assert row.attempts == 1
    assert row.requested_at == START
    assert row.accepted_at == START
    assert row.error_class is None and row.http_status is None
    assert outcome.status == "sent"
    assert outcome.provider_message_id == "provider-42"
    assert outcome.log_id == row.id
    assert channel.calls == [MESSAGE]


def test_file_channel_success_with_no_provider_id_is_sent(db):
    service = make_service(db, FakeChannel([None], name="file"))
    outcome = service.send("test:1", "test", MESSAGE)
    (row,) = rows(db)
    assert outcome.status == "sent"
    assert row.status == "sent"
    assert row.provider_message_id is None
    assert row.accepted_at is not None


def test_sent_key_is_not_sent_again(db):
    clock = FixedClock()
    channel = FakeChannel(["provider-1", "provider-2"])
    service = make_service(db, channel, clock)
    first = service.send("alert:gw1:k", "alert", MESSAGE)
    before = rows(db)

    clock.advance(timedelta(minutes=5))
    second = service.send("alert:gw1:k", "alert", Message(title="Inny", text="Inny"))

    assert len(channel.calls) == 1
    assert rows(db) == before
    assert second.status == "already_sent"
    assert second.provider_message_id == "provider-1"
    assert second.log_id == first.log_id
    assert second.requested_at == first.requested_at
    assert second.accepted_at == first.accepted_at


def test_failed_key_is_retried_in_the_same_row(db):
    channel = FakeChannel([ChannelRejectedError("rejected", 422), "provider-9"])
    service = make_service(db, channel)

    failed = service.send("alert:gw1:k", "alert", MESSAGE)
    assert failed.status == "failed"
    assert failed.error_class == "ChannelRejectedError"
    assert failed.http_status == 422
    assert failed.provider_message_id is None
    (row,) = rows(db)
    assert row.status == "failed" and row.attempts == 1

    second = Message(title="Poprawiony tytul", text="Poprawiona tresc", html=None)
    sent = service.send("alert:gw1:k", "alert", second)

    (row,) = rows(db)
    assert sent.status == "sent"
    assert row.status == "sent"
    assert row.attempts == 2
    assert row.provider_message_id == "provider-9"
    assert row.error_class is None and row.http_status is None
    assert row.title == "Poprawiony tytul"
    assert row.text_body == "Poprawiona tresc"
    assert row.html_body is None
    assert row.id == failed.log_id


def test_stop_event_ends_as_failed(db):
    stop = threading.Event()
    stop.set()
    channel = FakeChannel()
    service = DeliveryService(db, channel, FixedClock(), stop)

    outcome = service.send("alert:gw1:k", "alert", MESSAGE)

    (row,) = rows(db)
    assert outcome.status == "failed"
    assert outcome.error_class == "DeliveryStopped"
    assert row.status == "failed"
    assert row.attempts == 0
    assert channel.calls == []


def test_concurrent_sends_make_one_provider_call(db):
    gate = threading.Event()
    entered = threading.Event()
    channel = FakeChannel(["provider-1"], gate=gate, entered=entered)
    service = make_service(db, channel)
    outcomes = []

    def run():
        outcomes.append(service.send("alert:gw1:k", "alert", MESSAGE))

    first = threading.Thread(target=run)
    first.start()
    assert entered.wait(10)
    second = threading.Thread(target=run)
    second.start()

    deadline = time.monotonic() + 10
    waiting = False
    while time.monotonic() < deadline and not waiting:
        with db.connect() as conn:
            waiting = bool(
                conn.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
                ).scalar()
            )
        time.sleep(0.05)
    assert waiting, "the second sender never waited on the row lock"
    gate.set()
    first.join(15)
    second.join(15)

    assert len(channel.calls) == 1
    assert sorted(outcome.status for outcome in outcomes) == ["already_sent", "sent"]
    assert len(rows(db)) == 1


def test_disabled_returns_disabled_writes_nothing_logs_once(db, caplog):
    service = make_service(db, None)
    with caplog.at_level(logging.INFO):
        first = service.send("alert:gw1:k", "alert", MESSAGE)
        second = service.send("alert:gw1:k", "alert", MESSAGE)
    assert first.status == "disabled" and second.status == "disabled"
    assert first.log_id is None
    assert rows(db) == []
    notices = [record for record in caplog.records if "delivery disabled" in record.getMessage()]
    assert len(notices) == 1


def test_transient_errors_retried_three_times(db):
    clock = FixedClock()
    error = ChannelUnavailableError("down", 503)
    channel = FakeChannel([error])
    outcome = make_service(db, channel, clock).send("alert:gw1:k", "alert", MESSAGE)

    (row,) = rows(db)
    assert outcome.status == "failed"
    assert len(channel.calls) == 3
    assert clock.sleeps == [2.0, 4.0]
    assert row.status == "failed"
    assert row.attempts == 3
    assert row.error_class == "ChannelUnavailableError"
    assert row.http_status == 503

    recovering = FakeChannel([ChannelUnavailableError("down"), "provider-3"])
    clock = FixedClock()
    outcome = make_service(db, recovering, clock).send("alert:gw1:other", "alert", MESSAGE)
    assert outcome.status == "sent"
    assert outcome.attempts == 2
    assert clock.sleeps == [2.0]


@pytest.mark.parametrize(("retry_after", "expected"), [(3.0, 3.0), (60.0, 10.0), (None, 2.0)])
def test_rate_limit_waits_retry_after_capped(db, retry_after, expected):
    clock = FixedClock()
    channel = FakeChannel([ChannelRateLimitedError("slow down", retry_after), "provider-1"])
    outcome = make_service(db, channel, clock).send("alert:gw1:k", "alert", MESSAGE)
    assert outcome.status == "sent"
    assert clock.sleeps == [expected]


def test_other_4xx_not_retried(db):
    clock = FixedClock()
    channel = FakeChannel([ChannelRejectedError("bad", 422)])
    outcome = make_service(db, channel, clock).send("alert:gw1:k", "alert", MESSAGE)
    assert outcome.status == "failed"
    assert outcome.attempts == 1
    assert outcome.http_status == 422
    assert len(channel.calls) == 1
    assert clock.sleeps == []


def test_unexpected_error_becomes_failed(db):
    channel = FakeChannel([RuntimeError("boom")])
    outcome = make_service(db, channel).send("alert:gw1:k", "alert", MESSAGE)
    (row,) = rows(db)
    assert outcome.status == "failed"
    assert outcome.error_class == "RuntimeError"
    assert row.error_class == "RuntimeError"
    assert len(channel.calls) == 1


def test_logs_carry_no_addresses_title_body_or_key(db, caplog):
    api_key = "re_sentinel_api_key_000"
    sender = "sentinel-sender@example.test"
    recipient = "sentinel-recipient@example.test"
    message = Message(
        title="Sentinel title", text="Sentinel plain body", html="<p>Sentinel html body</p>"
    )
    fail = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["on"]:
            return httpx.Response(500, json={"message": f"{sender} {recipient} {api_key}"})
        return httpx.Response(200, json={"id": "provider-77"})

    channel = ResendChannel(api_key, sender, recipient, transport=httpx.MockTransport(handler))
    service = make_service(db, channel)
    with caplog.at_level(logging.DEBUG):
        ok = service.send("presser:sentinel-key", "presser", message)
        fail["on"] = True
        bad = service.send("alert:sentinel-key", "alert", message)

    assert ok.status == "sent" and bad.status == "failed"
    for secret in (
        api_key,
        sender,
        recipient,
        message.title,
        message.text,
        message.html,
        "sentinel-key",
    ):
        assert secret not in caplog.text

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("delivery kind=")]
    assert len(lines) == 2
    assert (
        f"kind=presser log_id={ok.log_id} channel=resend status=sent attempts=1 error=None"
        in lines[0]
    )
    assert (
        f"kind=alert log_id={bad.log_id} channel=resend status=failed attempts=3"
        " error=ChannelUnavailableError" in lines[1]
    )
