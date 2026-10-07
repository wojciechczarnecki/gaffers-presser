from collections.abc import Iterator
from dataclasses import dataclass, replace
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
    reposted_author_handle: str | None = None
    embedded: bool = False
    entry_head: bool = True
    quoted_x_id: int | None = None


def merge_fetched(first: FetchedPost, second: FetchedPost) -> FetchedPost:
    base = second if first.embedded and not second.embedded else first
    return replace(
        base,
        embedded=first.embedded and second.embedded,
        entry_head=first.entry_head or second.entry_head,
        quoted_x_id=first.quoted_x_id or second.quoted_x_id,
    )


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
