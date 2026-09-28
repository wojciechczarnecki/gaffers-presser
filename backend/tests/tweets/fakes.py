from collections.abc import Iterator

from app.tweets.sources.base import FetchedPost


class FakeSource:
    def __init__(self, pages: list[list[FetchedPost] | Exception], max_pages: int = 5) -> None:
        self.name = "fake"
        self.max_pages = max_pages
        self._pages = pages
        self.pull_count = 0
        self.closed = False

    def pages(self, list_id: int) -> Iterator[list[FetchedPost]]:
        for page in self._pages:
            self.pull_count += 1
            if isinstance(page, Exception):
                raise page
            yield page

    def close(self) -> None:
        self.closed = True


def post(
    x_id: int,
    author_handle: str = "synthetic_leaker",
    text: str = "synthetic text",
    created_at=None,
    is_repost: bool = False,
    is_reply: bool = False,
    raw: dict | None = None,
) -> FetchedPost:
    from datetime import UTC, datetime

    return FetchedPost(
        x_id=x_id,
        author_handle=author_handle,
        text=text,
        created_at=created_at or datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC),
        is_repost=is_repost,
        is_reply=is_reply,
        raw=raw or {"id": x_id},
    )
