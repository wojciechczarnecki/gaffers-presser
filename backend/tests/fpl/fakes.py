from collections.abc import Callable
from typing import Any

import httpx

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
