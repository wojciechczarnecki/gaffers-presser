import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.clock import StopAwareClock
from app.fpl.models import Gameweek, Season
from app.tweets.loop import TweetPoller, start_poller
from app.tweets.membership import latest_snapshot
from app.tweets.models import ListMembership, TweetPoll
from app.tweets.sources.base import (
    MembershipNotSupportedError,
    SourceRateLimitedError,
    SourceUnavailableError,
)
from app.worker.jobs import Shutdown
from tests.tweets.fakes import FakeSource
from tests.tweets.membership_helpers import set_members
from tests.tweets.payloads import load
from tests.worker.sim import FakeClock

DEADLINE = datetime(2026, 10, 10, 18, 30, 0, tzinfo=UTC)


class ScriptedSource:
    def __init__(self, scripts: list) -> None:
        self.name = "fake"
        self.max_pages = 5
        self._scripts = list(scripts)
        self._i = 0
        self.closed = False

    def pages(self, list_id: int):
        script = self._scripts[self._i]
        self._i += 1
        if isinstance(script, Exception):
            raise script
        return iter(script)

    def members(self, list_id: int):
        raise MembershipNotSupportedError("fake: membership not supported")

    def close(self) -> None:
        self.closed = True


def _seed_gameweek(db, deadline_at: datetime) -> None:
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        session.add(
            Gameweek(
                season="2026/27",
                fpl_id=6,
                name="GW6",
                deadline_at=deadline_at,
                finished=False,
                data_checked=False,
            )
        )


def _poll_times(db) -> list[tuple[datetime, str]]:
    with Session(db) as session:
        rows = session.exec(select(TweetPoll).order_by(TweetPoll.started_at)).all()
    return [(row.started_at, row.outcome) for row in rows]


def test_polls_at_window_then_sparse_interval(db):
    _seed_gameweek(db, DEADLINE)
    start = DEADLINE - timedelta(seconds=100)
    end = DEADLINE + timedelta(minutes=40, seconds=1)
    clock = FakeClock(start, end)
    stop_event = threading.Event()
    source = FakeSource(pages=[[]])
    poller = TweetPoller(db, lambda: source, list_id=123, clock=clock, stop_event=stop_event)

    with pytest.raises(Shutdown):
        poller.run()

    times = [t for t, _outcome in _poll_times(db)]
    assert times == [
        start,
        start + timedelta(seconds=20),
        start + timedelta(seconds=40),
        start + timedelta(seconds=60),
        start + timedelta(seconds=80),
        DEADLINE,
        DEADLINE + timedelta(minutes=30),
    ]


def test_build_failure_is_logged_and_retried(db, caplog):
    calls = {"n": 0}
    source = FakeSource(pages=[[]])

    def make_source():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return source

    start = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=61)
    clock = FakeClock(start, end)
    stop_event = threading.Event()
    poller = TweetPoller(db, make_source, list_id=1, clock=clock, stop_event=stop_event)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(Shutdown):
            poller.run()

    assert calls["n"] == 2
    assert "tweet poller iteration failed: RuntimeError" in caplog.text
    assert len(_poll_times(db)) == 1


def test_failures_do_not_stop_polling(db):
    scripts = [
        SourceUnavailableError("down"),
        [],
        SourceRateLimitedError("rate limited", retry_after=3600),
        [],
    ]
    source = ScriptedSource(scripts)
    start = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=5401)
    clock = FakeClock(start, end)
    stop_event = threading.Event()
    poller = TweetPoller(db, lambda: source, list_id=1, clock=clock, stop_event=stop_event)

    with pytest.raises(Shutdown):
        poller.run()

    rows = _poll_times(db)
    # The hour-long retry-after is capped at the sparse interval.
    assert [t for t, _outcome in rows] == [
        start,
        start + timedelta(seconds=1800),
        start + timedelta(seconds=3600),
        start + timedelta(seconds=5400),
    ]
    assert [outcome for _t, outcome in rows] == [
        "failed",
        "succeeded",
        "rate_limited",
        "succeeded",
    ]


def test_unrecorded_poll_still_delays_the_next_one(db, monkeypatch):
    monkeypatch.setattr("app.tweets.ingest.write_poll", lambda engine, record: None)
    _seed_gameweek(db, DEADLINE)
    start = DEADLINE - timedelta(seconds=100)
    end = start + timedelta(seconds=41)
    clock = FakeClock(start, end)
    stop_event = threading.Event()

    class CountingSource(FakeSource):
        def pages(self, list_id: int):
            if self.pull_count >= 10:
                stop_event.set()
            return super().pages(list_id)

    source = CountingSource(pages=[[]])
    poller = TweetPoller(db, lambda: source, list_id=1, clock=clock, stop_event=stop_event)

    with pytest.raises(Shutdown):
        poller.run()

    assert source.pull_count == 3
    assert _poll_times(db) == []


def test_deadline_added_mid_run_is_picked_up_within_60_s(db):
    start = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    deadline = start + timedelta(minutes=40)
    end = start + timedelta(seconds=90)

    class DeadlineAddingClock(FakeClock):
        def __init__(self, start, end) -> None:
            super().__init__(start, end)
            self._elapsed = timedelta(0)
            self._added = False

        def sleep(self, seconds: float) -> None:
            self._elapsed += timedelta(seconds=seconds)
            if not self._added and self._elapsed >= timedelta(seconds=30):
                self._added = True
                _seed_gameweek(db, deadline)
            super().sleep(seconds)

    clock = DeadlineAddingClock(start, end)
    stop_event = threading.Event()
    source = FakeSource(pages=[[]])
    poller = TweetPoller(db, lambda: source, list_id=1, clock=clock, stop_event=stop_event)

    with pytest.raises(Shutdown):
        poller.run()

    times = [t for t, _outcome in _poll_times(db)]
    assert times == [
        start,
        start + timedelta(seconds=60),
        start + timedelta(seconds=80),
    ]


def test_close_runs_on_the_poller_thread(db):
    close_thread_names = []
    built = threading.Event()

    class RecordingSource(FakeSource):
        def close(self) -> None:
            close_thread_names.append(threading.current_thread().name)
            super().close()

    source = RecordingSource(pages=[[]])

    def make_source():
        built.set()
        return source

    stop_event = threading.Event()
    thread = start_poller(db, make_source, list_id=1, stop_event=stop_event)
    assert built.wait(timeout=5)
    stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert close_thread_names == ["tweet-poller"]
    assert source.closed


def test_stop_event_ends_loop_promptly(db):
    source = FakeSource(pages=[[]])
    stop_event = threading.Event()
    sleeping = threading.Event()

    class SignallingClock(StopAwareClock):
        def sleep(self, seconds: float) -> None:
            sleeping.set()
            super().sleep(seconds)

    thread = start_poller(
        db, lambda: source, list_id=1, stop_event=stop_event, clock=SignallingClock(stop_event)
    )
    assert sleeping.wait(timeout=5)

    start = time.monotonic()
    stop_event.set()
    thread.join(timeout=1)
    elapsed = time.monotonic() - start

    assert not thread.is_alive()
    assert elapsed < 1


def test_poller_uses_window_and_extra_deadlines(db):
    rehearsal = DEADLINE + timedelta(days=2)
    start = rehearsal - timedelta(minutes=120)
    clock = FakeClock(start, start + timedelta(seconds=61))
    source = FakeSource(pages=[[]])
    poller = TweetPoller(
        db,
        lambda: source,
        list_id=123,
        clock=clock,
        stop_event=threading.Event(),
        window=timedelta(minutes=130),
        extra_deadlines=(rehearsal,),
    )

    with pytest.raises(Shutdown):
        poller.run()

    assert [t for t, _ in _poll_times(db)] == [
        start + timedelta(seconds=n) for n in (0, 20, 40, 60)
    ]


def test_poller_without_a_custom_window_stays_sparse_at_two_hours(db):
    rehearsal = DEADLINE + timedelta(days=2)
    start = rehearsal - timedelta(minutes=120)
    clock = FakeClock(start, start + timedelta(seconds=61))
    source = FakeSource(pages=[[]])
    poller = TweetPoller(db, lambda: source, list_id=123, clock=clock, stop_event=threading.Event())

    with pytest.raises(Shutdown):
        poller.run()

    assert [t for t, _ in _poll_times(db)] == [start]


def _floors_of_one_poll(db, monkeypatch, now, extra_deadlines=()):
    from app.tweets import loop

    floors = []
    real_poll_once = loop.poll_once

    def spy(*args):
        floors.append(args[-1])
        return real_poll_once(*args)

    monkeypatch.setattr(loop, "poll_once", spy)
    poller = TweetPoller(
        db,
        lambda: FakeSource(pages=[[]]),
        list_id=123,
        clock=FakeClock(now, now + timedelta(seconds=1)),
        stop_event=threading.Event(),
        extra_deadlines=extra_deadlines,
        max_lookback=timedelta(days=5),
    )
    with pytest.raises(Shutdown):
        poller.run()
    return floors


def _seed_gw5_and_gw6(db, gw5_deadline: datetime) -> None:
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        for fpl_id, deadline_at in ((5, gw5_deadline), (6, DEADLINE)):
            session.add(
                Gameweek(
                    season="2026/27",
                    fpl_id=fpl_id,
                    name=f"GW{fpl_id}",
                    deadline_at=deadline_at,
                    finished=False,
                    data_checked=False,
                )
            )


@pytest.mark.parametrize(
    ("gw5_before_gw6", "expected_floor_before_gw6"),
    [(timedelta(days=3), timedelta(days=3)), (timedelta(days=10), timedelta(days=5))],
)
def test_poll_gets_the_alert_window_floor(
    db, monkeypatch, gw5_before_gw6, expected_floor_before_gw6
):
    _seed_gw5_and_gw6(db, DEADLINE - gw5_before_gw6)
    floors = _floors_of_one_poll(db, monkeypatch, now=DEADLINE - timedelta(days=1))
    assert floors == [DEADLINE - expected_floor_before_gw6]


def test_a_past_rehearsal_deadline_does_not_move_the_floor(db, monkeypatch):
    gw5 = DEADLINE - timedelta(days=3)
    _seed_gw5_and_gw6(db, gw5)
    now = DEADLINE - timedelta(days=1)
    floors = _floors_of_one_poll(
        db, monkeypatch, now=now, extra_deadlines=(now - timedelta(hours=2),)
    )
    assert floors == [gw5]


class _CountingClock(FakeClock):
    def __init__(self, start, end, db) -> None:
        super().__init__(start, end)
        self._db = db
        self.rows_at: dict[datetime, int] = {}

    def sleep(self, seconds: float) -> None:
        with Session(self._db) as session:
            self.rows_at[self.now()] = len(session.exec(select(ListMembership)).all())
        super().sleep(seconds)


def _members_api():
    import httpx

    from tests.tweets.sources.test_twscrape_source import FakeApi

    return FakeApi([], member_pages=[httpx.Response(200, json=load("twscrape-list-members"))])


def test_membership_fetched_at_start_and_every_6_hours(db):
    from app.tweets.sources.twscrape_source import TwscrapeSource

    start = datetime(2026, 10, 7, 0, 0, 0, tzinfo=UTC)
    clock = _CountingClock(start, start + timedelta(hours=6, minutes=2), db)
    source = TwscrapeSource("dedicated", "auth_token=a; ct0=b", ":unused:", api=_members_api())
    poller = TweetPoller(db, lambda: source, list_id=1, clock=clock, stop_event=threading.Event())

    with pytest.raises(Shutdown):
        poller.run()

    assert clock.rows_at[start + timedelta(minutes=1)] == 1
    assert clock.rows_at[start + timedelta(hours=5, minutes=59)] == 1
    assert clock.rows_at[start + timedelta(hours=6, minutes=1)] == 2
    with Session(db) as session:
        snapshot = latest_snapshot(session)
    assert snapshot.handles == frozenset(
        {"synthetic_leaker_1", "synthetic_leaker_2", "synthetic_leaker_3"}
    )
    assert snapshot.fetched_at == start + timedelta(hours=6)


def test_failed_membership_fetch_keeps_snapshot_and_polling(db, caplog):
    set_members(db, ["kept_member"])
    start = datetime(2026, 10, 7, 0, 0, 0, tzinfo=UTC)
    clock = FakeClock(start, start + timedelta(minutes=31))
    source = FakeSource(pages=[[]], members=SourceRateLimitedError("limited"))
    poller = TweetPoller(db, lambda: source, list_id=1, clock=clock, stop_event=threading.Event())

    with caplog.at_level(logging.WARNING):
        with pytest.raises(Shutdown):
            poller.run()

    assert "list membership fetch failed: SourceRateLimitedError" in caplog.text
    assert source.members_calls == 2
    with Session(db) as session:
        assert latest_snapshot(session).handles == frozenset({"kept_member"})
        assert len(session.exec(select(ListMembership)).all()) == 1
    assert len(_poll_times(db)) == 2
    assert all(outcome == "succeeded" for _t, outcome in _poll_times(db))


def test_unsupported_source_records_a_null_snapshot_once(db):
    start = datetime(2026, 10, 7, 0, 0, 0, tzinfo=UTC)
    clock = FakeClock(start, start + timedelta(hours=7))
    source = FakeSource(pages=[[]])
    poller = TweetPoller(db, lambda: source, list_id=1, clock=clock, stop_event=threading.Event())

    with pytest.raises(Shutdown):
        poller.run()

    assert source.members_calls == 1
    with Session(db) as session:
        rows = session.exec(select(ListMembership)).all()
    assert [row.handles for row in rows] == [None]
