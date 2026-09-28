import logging
import threading

from sqlalchemy import Engine
from sqlmodel import Session

from app.extraction.service import ExtractionRuntime, extract_post
from app.extraction.store import next_pending
from app.extraction.tracing import flush, make_handler
from app.tweets.loop import Clock, StopAwareClock
from app.worker.jobs import Shutdown

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 2.0
ITERATION_ERROR_SLEEP_SECONDS = 30.0


class ExtractionLoop:
    def __init__(
        self,
        engine: Engine,
        runtime: ExtractionRuntime,
        clock: Clock,
        stop_event: threading.Event,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._clock = clock
        self._stop_event = stop_event

    def run(self) -> None:
        handler = make_handler(self._runtime.tracing)
        try:
            while not self._stop_event.is_set():
                try:
                    with Session(self._engine) as session:
                        post = next_pending(session)
                    if post is None:
                        self._clock.sleep(IDLE_POLL_SECONDS)
                        continue
                    extract_post(
                        self._engine,
                        self._runtime,
                        post,
                        self._clock,
                        self._stop_event,
                        handler,
                        record_latency=True,
                    )
                except Exception as exc:
                    logger.error("extraction loop iteration failed: %s", type(exc).__name__)
                    self._clock.sleep(ITERATION_ERROR_SLEEP_SECONDS)
        finally:
            flush(handler)


def start_extractor(
    engine: Engine,
    runtime: ExtractionRuntime,
    stop_event: threading.Event,
    clock: Clock | None = None,
) -> threading.Thread:
    loop = ExtractionLoop(engine, runtime, clock or StopAwareClock(stop_event), stop_event)

    def target() -> None:
        try:
            loop.run()
        except Shutdown:
            pass

    thread = threading.Thread(target=target, name="extractor", daemon=True)
    thread.start()
    return thread
