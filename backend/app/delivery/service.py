import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock
from app.core.retry import with_retries
from app.delivery.channels.base import (
    Channel,
    ChannelRateLimitedError,
    ChannelUnavailableError,
    Message,
)
from app.delivery.models import DeliveryLog
from app.delivery.store import claim_row

logger = logging.getLogger(__name__)

Kind = Literal["alert", "presser", "test"]
KINDS = ("alert", "presser", "test")

MAX_WAIT_SECONDS = 10.0

_disabled_lock = threading.Lock()
_disabled_noticed = False


def _note_disabled() -> None:
    global _disabled_noticed
    with _disabled_lock:
        if _disabled_noticed:
            return
        _disabled_noticed = True
    logger.info("delivery disabled: DELIVERY_PROVIDER is empty")


@dataclass(frozen=True)
class SendOutcome:
    status: Literal["sent", "already_sent", "failed", "disabled"]
    log_id: int | None = None
    provider_message_id: str | None = None
    attempts: int = 0
    requested_at: datetime | None = None
    accepted_at: datetime | None = None
    error_class: str | None = None
    http_status: int | None = None


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, ChannelUnavailableError | ChannelRateLimitedError)


def _wait(exc: Exception, default: float) -> float:
    if isinstance(exc, ChannelRateLimitedError) and exc.retry_after is not None:
        return min(exc.retry_after, MAX_WAIT_SECONDS)
    return min(default, MAX_WAIT_SECONDS)


def _outcome(status: Literal["sent", "already_sent", "failed"], row: DeliveryLog) -> SendOutcome:
    return SendOutcome(
        status=status,
        log_id=row.id,
        provider_message_id=row.provider_message_id,
        attempts=row.attempts,
        requested_at=row.requested_at,
        accepted_at=row.accepted_at,
        error_class=row.error_class,
        http_status=row.http_status,
    )


class DeliveryService:
    def __init__(
        self,
        engine: Engine,
        channel: Channel | None,
        clock: Clock,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._engine = engine
        self._channel = channel
        self._clock = clock
        self._stop_event = stop_event or threading.Event()

    def send(self, key: str, kind: Kind, message: Message) -> SendOutcome:
        if not key or not key.strip():
            raise ValueError("idempotency key must not be empty")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
        channel = self._channel
        if channel is None:
            _note_disabled()
            return SendOutcome("disabled")

        with Session(self._engine) as session:
            row = claim_row(
                session,
                key=key,
                kind=kind,
                channel=channel.name,
                title=message.title,
                text_body=message.text,
                html_body=message.html,
                requested_at=self._clock.now(),
            )
            if row.status == "sent":
                result = _outcome("already_sent", row)
                self._log(kind, result, channel.name)
                return result

            row.kind = kind
            row.channel = channel.name
            row.title = message.title
            row.text_body = message.text
            row.html_body = message.html
            row.requested_at = self._clock.now()
            outcome = with_retries(
                lambda: channel.send(message, key),
                self._clock,
                self._stop_event,
                what="delivery",
                retryable=_retryable,
                wait=_wait,
            )
            row.attempts += outcome.attempts
            if outcome.error is None and not outcome.stopped:
                row.status = "sent"
                row.provider_message_id = outcome.result
                row.accepted_at = self._clock.now()
                row.error_class = None
                row.http_status = None
            else:
                row.status = "failed"
                row.provider_message_id = None
                row.accepted_at = None
                row.error_class = (
                    type(outcome.error).__name__ if outcome.error else "DeliveryStopped"
                )
                row.http_status = getattr(outcome.error, "http_status", None)
            session.add(row)
            session.commit()
            result = _outcome("sent" if row.status == "sent" else "failed", row)
        self._log(kind, result, channel.name)
        return result

    @staticmethod
    def _log(kind: str, outcome: SendOutcome, channel: str) -> None:
        logger.info(
            "delivery kind=%s log_id=%s channel=%s status=%s attempts=%s error=%s",
            kind,
            outcome.log_id,
            channel,
            outcome.status,
            outcome.attempts,
            outcome.error_class,
        )
