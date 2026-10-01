import logging
import threading
from collections.abc import Callable

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, StopAwareClock
from app.fpl.deadlines import upcoming_deadlines
from app.tweets.ingest import poll_once
from app.tweets.schedule import MAX_SLEEP, next_poll_at
from app.tweets.sources.base import TweetSource
from app.tweets.store import PollRecord, latest_poll
from app.worker.jobs import Shutdown

logger = logging.getLogger(__name__)


def _later(stored: PollRecord | None, remembered: PollRecord | None) -> PollRecord | None:
    if stored is None or remembered is None:
        return stored or remembered
    return remembered if remembered.started_at > stored.started_at else stored


class TweetPoller:
    def __init__(
        self,
        engine: Engine,
        make_source: Callable[[], TweetSource],
        list_id: int,
        clock: Clock,
        stop_event: threading.Event,
    ) -> None:
        self._engine = engine
        self._make_source = make_source
        self._list_id = list_id
        self._clock = clock
        self._stop_event = stop_event
        # The poll log write may fail while reads still work; the in-memory record keeps
        # the schedule from treating an unrecorded poll as never having happened.
        self._last_record: PollRecord | None = None

    def run(self) -> None:
        source: TweetSource | None = None
        try:
            while not self._stop_event.is_set():
                try:
                    if source is None:
                        source = self._make_source()
                    with Session(self._engine) as session:
                        deadlines = upcoming_deadlines(session, self._clock.now())
                    last = _later(latest_poll(self._engine, source.name), self._last_record)
                    now = self._clock.now()
                    next_at = next_poll_at(deadlines, last, now)
                    if next_at <= now:
                        self._last_record = poll_once(
                            self._engine, source, self._list_id, self._clock.now
                        )
                    else:
                        sleep_seconds = min(
                            (next_at - now).total_seconds(), MAX_SLEEP.total_seconds()
                        )
                        self._clock.sleep(sleep_seconds)
                except Exception as exc:
                    logger.error("tweet poller iteration failed: %s", type(exc).__name__)
                    self._clock.sleep(MAX_SLEEP.total_seconds())
        finally:
            if source is not None:
                source.close()


def start_poller(
    engine: Engine,
    make_source: Callable[[], TweetSource],
    list_id: int,
    stop_event: threading.Event,
    clock: Clock | None = None,
) -> threading.Thread:
    poller = TweetPoller(
        engine, make_source, list_id, clock or StopAwareClock(stop_event), stop_event
    )

    def target() -> None:
        try:
            poller.run()
        except Shutdown:
            pass

    thread = threading.Thread(target=target, name="tweet-poller", daemon=True)
    thread.start()
    return thread
