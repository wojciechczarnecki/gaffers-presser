from dataclasses import dataclass
from typing import Protocol

from app.core.errors import CollectorError


class InvalidMessageError(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    title: str
    text: str
    html: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvalidMessageError("message title must not be empty")
        if not self.text.strip():
            raise InvalidMessageError("message text must not be empty")


class Channel(Protocol):
    name: str

    def send(self, message: Message, idempotency_key: str) -> str | None:
        """Deliver the message; the same key on a repeated call must not deliver it twice
        when the provider supports deduplication (a lost reply followed by a retry)."""
        ...

    def close(self) -> None: ...


class ChannelError(CollectorError):
    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class ChannelUnavailableError(ChannelError):
    pass


class ChannelRateLimitedError(ChannelError):
    def __init__(
        self, message: str, retry_after: float | None = None, http_status: int | None = 429
    ) -> None:
        super().__init__(message, http_status)
        self.retry_after = retry_after


class ChannelRejectedError(ChannelError):
    pass


class ChannelPayloadError(ChannelError):
    pass
