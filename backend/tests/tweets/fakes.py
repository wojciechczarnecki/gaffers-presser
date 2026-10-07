from collections.abc import Callable, Iterator

import httpx

from app.tweets.sources.base import FetchedPost

Route = dict | httpx.Response | Callable[[httpx.Request], httpx.Response]


class FakeHttp:
    def __init__(self, routes: dict[str, Route] | None = None) -> None:
        self.routes: dict[str, Route] = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def add(self, key: str, value: Route) -> None:
        self.routes[key] = value

    def _route_key(self, request: httpx.Request) -> str:
        path = request.url.path.lstrip("/")
        if request.url.query:
            return f"{path}?{request.url.query.decode()}"
        return path

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        value = self.routes.get(self._route_key(request))
        if value is None:
            return httpx.Response(404, json={"detail": "not found"})
        if callable(value):
            return value(request)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


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
    reposted_author_handle: str | None = None,
    embedded: bool = False,
    entry_head: bool = True,
    quoted_x_id: int | None = None,
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
        reposted_author_handle=reposted_author_handle,
        embedded=embedded,
        entry_head=entry_head,
        quoted_x_id=quoted_x_id,
    )
