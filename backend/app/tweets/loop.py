import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, StopAwareClock
from app.fpl.deadlines import (
    DEFAULT_MAX_LOOKBACK,
    deadline_at_or_before,
    next_deadline_after,
    upcoming_deadlines,
    window_floor,
)
from app.tweets.ingest import poll_once
from app.tweets.membership import (
    MEMBERSHIP_INTERVAL,
    MEMBERSHIP_RETRY,
    MembershipShrunkError,
    fetch_membership,
    store_membership,
)
from app.tweets.schedule import MAX_SLEEP, WINDOW, next_poll_at
from app.tweets.sources.base import TweetSource
from app.tweets.store import PollRecord, latest_poll
from app.worker.jobs import Shutdown

logger = logging.getLogger(__name__)

SlotTimes = Callable[[datetime], Sequence[datetime]]


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
        window: timedelta = WINDOW,
        extra_deadlines: tuple[datetime, ...] = (),
        max_lookback: timedelta = DEFAULT_MAX_LOOKBACK,
        slot_times: SlotTimes | None = None,
    ) -> None:
        self._engine = engine
        self._make_source = make_source
        self._list_id = list_id
        self._clock = clock
        self._stop_event = stop_event
        self._window = window
        self._extra_deadlines = list(extra_deadlines)
        self._max_lookback = max_lookback
        self._slot_times = slot_times
        # The poll log write may fail while reads still work; the in-memory record keeps
        # the schedule from treating an unrecorded poll as never having happened.
        self._last_record: PollRecord | None = None
        self._membership_due_at: datetime | None = None
        self._membership_unsupported = False

    def _refresh_membership(self, source: TweetSource) -> None:
        now = self._clock.now()
        if self._membership_unsupported or (
            self._membership_due_at is not None and now < self._membership_due_at
        ):
            return
        try:
            snapshot = fetch_membership(source, self._list_id, now)
        except Exception as exc:
            logger.warning("list membership fetch failed: %s", type(exc).__name__)
            self._membership_due_at = now + MEMBERSHIP_RETRY
            return
        try:
            store_membership(self._engine, snapshot)
        except MembershipShrunkError as exc:
            logger.warning("list membership snapshot refused: %s", exc)
            self._membership_due_at = now + MEMBERSHIP_RETRY
            return
        except Exception as exc:
            logger.warning("list membership save failed: %s", type(exc).__name__)
            self._membership_due_at = now + MEMBERSHIP_RETRY
            return
        if snapshot.handles is None:
            self._membership_unsupported = True
        else:
            self._membership_due_at = now + MEMBERSHIP_INTERVAL

    def run(self) -> None:
        source: TweetSource | None = None
        try:
            while not self._stop_event.is_set():
                try:
                    if source is None:
                        source = self._make_source()
                    self._refresh_membership(source)
                    with Session(self._engine) as session:
                        deadlines = sorted(
                            [
                                *upcoming_deadlines(session, self._clock.now()),
                                *self._extra_deadlines,
                            ]
                        )
                    last = _later(latest_poll(self._engine, source.name), self._last_record)
                    now = self._clock.now()
                    next_at = next_poll_at(deadlines, last, now, self._window, self._slot_times)
                    if next_at <= now:
                        # like the alert window, the previous deadline is a real gameweek
                        # only, never a rehearsal deadline
                        with Session(self._engine) as session:
                            previous = deadline_at_or_before(session, now)
                        floor = window_floor(
                            previous, next_deadline_after(deadlines, now), self._max_lookback, now
                        )
                        self._last_record = poll_once(
                            self._engine, source, self._list_id, self._clock.now, floor
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
    window: timedelta = WINDOW,
    extra_deadlines: tuple[datetime, ...] = (),
    max_lookback: timedelta = DEFAULT_MAX_LOOKBACK,
    slot_times: SlotTimes | None = None,
) -> threading.Thread:
    poller = TweetPoller(
        engine,
        make_source,
        list_id,
        clock or StopAwareClock(stop_event),
        stop_event,
        window,
        extra_deadlines,
        max_lookback,
        slot_times,
    )

    def target() -> None:
        try:
            poller.run()
        except Shutdown:
            pass

    thread = threading.Thread(target=target, name="tweet-poller", daemon=True)
    thread.start()
    return thread
