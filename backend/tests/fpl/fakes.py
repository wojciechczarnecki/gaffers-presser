from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select
from sqlmodel import Session, SQLModel

import app.fpl.models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.fpl.client import FplClient

Route = dict | httpx.Response | Callable[[httpx.Request], httpx.Response]


class FakeFpl:
    def __init__(self, routes: dict[str, Route] | None = None) -> None:
        self.routes: dict[str, Route] = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def add(self, path: str, value: Route) -> None:
        self.routes[path] = value

    def _route_key(self, request: httpx.Request) -> str:
        path = request.url.path.split("/api/", 1)[-1]
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

    def client(self, **kwargs: Any) -> FplClient:
        return FplClient(transport=self.transport(), **kwargs)


DEFAULT_EXCLUDE = {"observed_at", "fetched_at", "captured_at"}


def table_contents(session: Session, exclude: set[str] = DEFAULT_EXCLUDE) -> dict[str, list[tuple]]:
    contents: dict[str, list[tuple]] = {}
    for name, table in SQLModel.metadata.tables.items():
        cols = [c for c in table.columns if c.name not in exclude]
        rows = session.execute(select(*cols)).all()
        contents[name] = sorted(tuple(row) for row in rows)
    return contents
