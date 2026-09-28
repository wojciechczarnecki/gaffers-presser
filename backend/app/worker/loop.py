import logging
import threading
import time
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.fpl.client import FplClient
from app.worker.jobs import Shutdown, run_job
from app.worker.schedule import Job, PlannedAction, due_actions, missed_snapshot, plan
from app.worker.store import load_state

logger = logging.getLogger(__name__)

MAX_SLEEP_SECONDS = 3600.0


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class Worker:
    def __init__(
        self,
        engine: Engine,
        client: FplClient,
        league_ids: list[int],
        clock: Clock,
        stop_event: threading.Event | None = None,
        heartbeat=None,
    ) -> None:
        self._engine = engine
        self._client = client
        self._league_ids = league_ids
        self._clock = clock
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._heartbeat = heartbeat
        self._logged_missed: set[int] = set()

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise Shutdown

    def _run_job(self, action: PlannedAction) -> None:
        run_job(
            self._engine,
            self._client,
            self._league_ids,
            action,
            self._clock.now,
            self._stop_event,
        )

    def run(self) -> None:
        start_now = self._clock.now()
        self._run_job(
            PlannedAction(at=start_now, job=Job.reference_sync, gameweek=None, season=None)
        )
        self._check_stop()

        while True:
            if self._heartbeat is not None:
                self._heartbeat()

            try:
                state = load_state(self._engine)
            except SQLAlchemyError as exc:
                logger.error("state load failed: %s", type(exc).__name__)
                self._clock.sleep(60)
                self._check_stop()
                continue

            now = self._clock.now()
            missed = missed_snapshot(state, now)
            if missed is not None and missed not in self._logged_missed:
                logger.info("deadline snapshot missed: gameweek=%s", missed)
                self._logged_missed.add(missed)

            due = due_actions(state, now)
            if due:
                self._run_job(due[0])
                self._check_stop()
                continue

            actions = plan(state, now)
            next_at = min(a.at for a in actions)
            sleep_seconds = max(0.0, min((next_at - now).total_seconds(), MAX_SLEEP_SECONDS))
            self._clock.sleep(sleep_seconds)
            self._check_stop()
