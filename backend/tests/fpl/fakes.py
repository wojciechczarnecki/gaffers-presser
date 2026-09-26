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


STANDINGS_PAGE_SIZE = 50


def synthetic_league(
    league_id: int,
    entry_ids: list[int],
    *,
    gameweeks: list[int],
    player_ids: list[int],
    no_team_for: dict[int, set[int]] | None = None,
) -> dict[str, object]:
    no_team_for = no_team_for or {}
    routes: dict[str, object] = {}
    pages = [
        entry_ids[i : i + STANDINGS_PAGE_SIZE]
        for i in range(0, len(entry_ids), STANDINGS_PAGE_SIZE)
    ] or [[]]

    for page_num, page_entries in enumerate(pages, start=1):
        start_rank = (page_num - 1) * STANDINGS_PAGE_SIZE
        results = [
            {
                "entry": entry_id,
                "entry_name": f"Synthetic XI {entry_id}",
                "player_name": f"Synthetic Manager {entry_id}",
                "rank": start_rank + idx + 1,
                "event_total": 60,
                "total": 60 * len(gameweeks),
            }
            for idx, entry_id in enumerate(page_entries)
        ]
        routes[f"leagues-classic/{league_id}/standings/?page_standings={page_num}"] = {
            "league": {"id": league_id, "name": f"Synthetic League {league_id}"},
            "standings": {
                "has_next": page_num < len(pages),
                "page": page_num,
                "results": results,
            },
        }

    for entry_id in entry_ids:
        skip_gws = no_team_for.get(entry_id, set())
        for gw in gameweeks:
            if gw in skip_gws:
                routes[f"entry/{entry_id}/event/{gw}/picks/"] = httpx.Response(404)
                continue
            picks = [
                {
                    "element": player_ids[i % len(player_ids)],
                    "position": i + 1,
                    "multiplier": 2 if i == 0 else 1,
                    "is_captain": i == 0,
                    "is_vice_captain": i == 1,
                }
                for i in range(15)
            ]
            routes[f"entry/{entry_id}/event/{gw}/picks/"] = {
                "active_chip": None,
                "automatic_subs": [],
                "entry_history": {
                    "points": 60,
                    "total_points": 60 * gw,
                    "event_transfers": 1,
                    "event_transfers_cost": 0,
                    "points_on_bench": 2,
                    "bank": 5,
                    "value": 1000,
                    "overall_rank": 100000,
                },
                "picks": picks,
            }
        routes[f"entry/{entry_id}/history/"] = {
            "chips": [{"name": "wildcard", "time": "2026-08-25T12:00:00Z", "event": gameweeks[0]}]
        }
        routes[f"entry/{entry_id}/transfers/"] = [
            {
                "element_in": player_ids[0],
                "element_in_cost": 50,
                "element_out": player_ids[1 % len(player_ids)],
                "element_out_cost": 45,
                "event": gameweeks[0],
                "time": "2026-08-20T10:00:00Z",
            }
        ]
    return routes
