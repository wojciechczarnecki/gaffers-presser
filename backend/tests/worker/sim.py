import copy
from datetime import datetime, timedelta

import httpx
from sqlalchemy import Engine

from app.worker.jobs import Shutdown
from app.worker.models import JobRun
from app.worker.schedule import Job
from tests.fpl.fakes import FakeFpl, synthetic_league
from tests.fpl.payloads import load

LEAGUE_ID = 987654301
ENTRY_IDS = [880000001, 880000002]
PLAYER_IDS = list(range(1, 21))


def _trimmed_bootstrap() -> dict:
    payload = load("bootstrap-static")
    payload["elements"] = payload["elements"][:20]
    return payload


def _trimmed_fixtures() -> list[dict]:
    return [f for f in load("fixtures") if f["event"] in (5, 6, 7)]


def _trimmed_live() -> dict:
    live = load("event-3-live")
    live["elements"] = [el for el in live["elements"] if el["id"] in PLAYER_IDS]
    return live


class FakeClock:
    def __init__(self, start: datetime, end: datetime) -> None:
        self._now = start
        self._end = end

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        if self._now >= self._end:
            raise Shutdown


class SimulatedFpl:
    """A FakeFpl whose bootstrap-static/ route reflects the fake clock.

    `gameweek_overrides` maps a gameweek id to a list of (at, finished, data_checked)
    tuples applied in order once `now >= at`. `failure_windows` maps a route name
    (`bootstrap-static`, `fixtures`, `event-live`) to a list of (start, end) tuples during
    which that route returns 503.
    """

    def __init__(
        self,
        clock: FakeClock,
        gameweek_overrides: dict[int, list[tuple[datetime, bool, bool]]] | None = None,
        failure_windows: dict[str, list[tuple[datetime, datetime]]] | None = None,
    ) -> None:
        self._clock = clock
        self._gameweek_overrides = gameweek_overrides or {}
        self._failure_windows = failure_windows or {}
        self._base_bootstrap = _trimmed_bootstrap()
        self._fixtures = _trimmed_fixtures()
        self._live = _trimmed_live()

    def _failing(self, route: str) -> bool:
        now = self._clock.now()
        return any(start <= now < end for start, end in self._failure_windows.get(route, []))

    def _bootstrap_route(self, request: httpx.Request) -> httpx.Response:
        if self._failing("bootstrap-static"):
            return httpx.Response(503)
        payload = copy.deepcopy(self._base_bootstrap)
        now = self._clock.now()
        for event in payload["events"]:
            for at, finished, data_checked in self._gameweek_overrides.get(event["id"], []):
                if now >= at:
                    event["finished"] = finished
                    event["data_checked"] = data_checked
        return httpx.Response(200, json=payload)

    def _fixtures_route(self, request: httpx.Request) -> httpx.Response:
        if self._failing("fixtures"):
            return httpx.Response(503)
        return httpx.Response(200, json=self._fixtures)

    def _live_route(self, request: httpx.Request) -> httpx.Response:
        if self._failing("event-live"):
            return httpx.Response(503)
        return httpx.Response(200, json=self._live)

    def routes(self) -> dict:
        routes: dict = {
            "bootstrap-static/": self._bootstrap_route,
            "fixtures/": self._fixtures_route,
        }
        for gw in range(1, 39):
            routes[f"event/{gw}/live/"] = self._live_route
        routes.update(
            synthetic_league(
                LEAGUE_ID, ENTRY_IDS, gameweeks=list(range(1, 39)), player_ids=PLAYER_IDS
            )
        )
        return routes

    def fake(self) -> FakeFpl:
        return FakeFpl(self.routes())

    def client(self):
        return self.fake().client(sleep=lambda _: None, max_attempts=1)


def seed_done(engine: Engine, season: str, gameweeks: list[int], before: datetime) -> None:
    from sqlmodel import Session

    with Session(engine) as session, session.begin():
        for gw in gameweeks:
            for job in (Job.results_sync, Job.league_sync):
                session.add(
                    JobRun(
                        job=job.value,
                        season=season,
                        gameweek_fpl_id=gw,
                        started_at=before,
                        finished_at=before + timedelta(seconds=5),
                        outcome="succeeded",
                    )
                )


def run_until(worker) -> None:
    try:
        worker.run()
    except Shutdown:
        pass
