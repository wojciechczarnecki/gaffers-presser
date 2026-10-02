import logging
import threading
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import Session

from app.alerts.breaking import newest_extraction, run_breaking
from app.alerts.schedule import (
    MAX_WAKE_SECONDS,
    alert_deadlines,
    breaking_open,
    due_slots,
    next_alert_deadline,
    next_wake,
)
from app.alerts.service import AlertsRuntime, run_slot
from app.alerts.store import done_slots
from app.core.clock import Clock, StopAwareClock
from app.worker.jobs import Shutdown
from app.worker.store import load_state

logger = logging.getLogger(__name__)

ITERATION_ERROR_SLEEP_SECONDS = 30.0


class AlertLoop:
    def __init__(
        self,
        engine: Engine,
        runtime: AlertsRuntime,
        clock: Clock,
        stop_event: threading.Event,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._clock = clock
        self._stop_event = stop_event
        # deadline key -> newest extraction a completed breaking pass has seen
        self._processed_until: dict[str, datetime] = {}

    def tick(self) -> float:
        slots = self._runtime.config.slots
        state = load_state(self._engine)
        deadlines = alert_deadlines(
            state.season, state.gameweeks, self._runtime.config.rehearsal_deadline
        )
        deadline = next_alert_deadline(deadlines, self._clock.now())
        if deadline is None:
            return MAX_WAKE_SECONDS
        try:
            with Session(self._engine) as session:
                done = done_slots(session, deadline.key)
            for slot in due_slots(deadline, slots, set(done), self._clock.now()):
                run_slot(self._engine, self._runtime, deadline, slots.index(slot), self._clock)
            with Session(self._engine) as session:
                done = done_slots(session, deadline.key)
            if slots[-1] in done and breaking_open(deadline, True, self._clock.now()):
                with Session(self._engine) as session:
                    newest = newest_extraction(session)
                run_breaking(
                    self._engine,
                    self._runtime,
                    deadline,
                    done[slots[-1]].as_of,
                    self._clock,
                    processed_until=self._processed_until.get(deadline.key),
                )
                if newest is not None:
                    self._processed_until[deadline.key] = newest
        finally:
            self._runtime.corroboration.tracer.flush()
        return next_wake(deadline, slots, set(done), self._clock.now())

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                wake = self.tick()
            except Shutdown:
                raise
            except Exception as exc:
                logger.error("alerts loop iteration failed: %s", type(exc).__name__)
                wake = ITERATION_ERROR_SLEEP_SECONDS
            self._clock.sleep(wake)


def start_alerts(
    engine: Engine,
    runtime: AlertsRuntime,
    stop_event: threading.Event,
    clock: Clock | None = None,
) -> threading.Thread:
    loop = AlertLoop(engine, runtime, clock or StopAwareClock(stop_event), stop_event)

    def target() -> None:
        try:
            loop.run()
        except Shutdown:
            pass

    thread = threading.Thread(target=target, name="alerts", daemon=True)
    thread.start()
    return thread
