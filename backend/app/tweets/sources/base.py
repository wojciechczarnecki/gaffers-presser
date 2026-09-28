from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.errors import CollectorError


@dataclass(frozen=True)
class FetchedPost:
    x_id: int
    author_handle: str
    text: str
    created_at: datetime
    is_repost: bool
    is_reply: bool
    raw: dict


class TweetSource(Protocol):
    name: str
    max_pages: int

    def pages(self, list_id: int) -> Iterator[list[FetchedPost]]: ...

    def close(self) -> None: ...


class SourceUnavailableError(CollectorError):
    pass


class SourcePayloadError(CollectorError):
    pass


class SourceRateLimitedError(CollectorError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
