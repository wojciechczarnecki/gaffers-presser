import contextlib
import logging
import threading
from datetime import time as clock_time
from datetime import timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.alerts import loop as loop_module
from app.alerts.config import AlertConfig, WallClockSlot
from app.alerts.loop import AlertLoop, start_alerts
from app.alerts.models import Alert
from app.alerts.schedule import resolve_slots
from app.alerts.status import alert_status
from app.delivery.models import DeliveryLog
from app.worker.jobs import Shutdown
from tests.alerts.helpers import alerts_runtime
from tests.alerts.test_slots import DEADLINE_AT, seed
from tests.corroboration.helpers import NOW, SAKA, add_claim
from tests.delivery.fakes import FakeChannel, FixedClock

DEFAULT_CONFIG = AlertConfig((WallClockSlot(1, clock_time(20, 0)), 60), 3, Decimal("15"), None)


def minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


class VirtualClock:
    """Time moves only in sleep(); hooks run at the start of a sleep that crosses their time."""

    def __init__(self, start, end):
        self._now = start
        self._end = end
        self.hooks = []

    def now(self):
        return self._now

    def sleep(self, seconds):
        started = self._now
        self._now += timedelta(seconds=seconds)
        for at, hook in list(self.hooks):
            if self._now >= at:
                self.hooks.remove((at, hook))
                hook(started + timedelta(seconds=1))
        if self._now >= self._end:
            raise Shutdown


def alert_rows(db):
    with Session(db) as session:
        return list(session.exec(select(Alert).order_by(Alert.id)).all())


def test_breaking_sent_within_15_s_of_extraction(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(60), author="a1")
    clock = VirtualClock(NOW, DEADLINE_AT - minutes(5))
    channel = FakeChannel(["msg-1"])
    runtime = alerts_runtime(db, channel, clock)
    finished = {}

    def extract_post(at):
        finished["at"] = at
        add_claim(db, 2, SAKA, "out", created_at=at - minutes(2), author="late2")
        with db.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE extraction SET finished_at = %(at)s WHERE tweet_x_id = 2", {"at": at}
            )

    clock.hooks.append((NOW + minutes(100), extract_post))

    with contextlib.suppress(Shutdown):
        AlertLoop(db, runtime, clock, threading.Event()).run()

    assert [r.key for r in alert_rows(db)] == [
        "alert:2026/27:gw6:digest:120",
        "alert:2026/27:gw6:news:30",
        "alert:2026/27:gw6:breaking:2",
    ]
    assert channel.keys[-1] == "alert:2026/27:gw6:breaking:2"
    with Session(db) as session:
        accepted = session.exec(
            select(DeliveryLog.accepted_at).where(
                DeliveryLog.idempotency_key == "alert:2026/27:gw6:breaking:2"
            )
        ).one()
    delay = accepted - finished["at"]
    assert timedelta(0) < delay <= timedelta(seconds=15)


def test_tick_runs_due_slots_then_breaking(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(60), author="a1")
    clock = FixedClock(DEADLINE_AT - minutes(20))
    channel = FakeChannel(["msg-1"])
    loop = AlertLoop(db, alerts_runtime(db, channel, clock), clock, threading.Event())

    assert loop.tick() == 5.0
    assert [(r.kind, r.status) for r in alert_rows(db)] == [
        ("digest", "sent"),
        ("news", "skipped"),
    ]

    add_claim(
        db,
        2,
        SAKA,
        "out",
        created_at=clock.now() - minutes(1),
        finished=int(minutes(101).total_seconds()),
    )
    clock.advance(timedelta(seconds=5))

    assert loop.tick() == 5.0
    assert channel.keys[-1] == "alert:2026/27:gw6:breaking:2"
    assert loop.tick() == 5.0
    assert len(channel.keys) == 2


def test_nothing_runs_without_a_deadline(db):
    clock = FixedClock(NOW)
    channel = FakeChannel(["msg-1"])
    loop = AlertLoop(db, alerts_runtime(db, channel, clock), clock, threading.Event())

    assert loop.tick() == 60.0
    assert channel.calls == [] and alert_rows(db) == []


def test_tick_error_does_not_stop_the_loop(db, monkeypatch, caplog):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(60), author="a1")
    clock = VirtualClock(NOW, NOW + minutes(3))
    channel = FakeChannel(["msg-1"])
    runtime = alerts_runtime(db, channel, clock)
    real = loop_module.run_slot
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("secret detail that must not be logged")
        return real(*args, **kwargs)

    monkeypatch.setattr(loop_module, "run_slot", flaky)

    with caplog.at_level(logging.INFO), contextlib.suppress(Shutdown):
        AlertLoop(db, runtime, clock, threading.Event()).run()

    assert len(calls) >= 2
    assert [r.kind for r in alert_rows(db)] == ["digest"]
    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text


def test_logs_carry_no_bodies_addresses_or_texts(db, caplog):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(60), author="distinctivehandle")
    clock = FixedClock(DEADLINE_AT - minutes(20))
    channel = FakeChannel(["msg-1"])
    loop = AlertLoop(db, alerts_runtime(db, channel, clock), clock, threading.Event())

    with caplog.at_level(logging.DEBUG):
        loop.tick()
        add_claim(
            db,
            2,
            SAKA,
            "out",
            created_at=clock.now() - minutes(1),
            author="otherhandle",
            text="distinctive post text",
            finished=int(minutes(101).total_seconds()),
        )
        clock.advance(timedelta(seconds=5))
        loop.tick()

    assert len(channel.calls) == 2
    logged = caplog.text
    forbidden = [
        "distinctivehandle",
        "otherhandle",
        "distinctive post text",
        "owner@example.test",
        "Jan Kowalski",
        "Kowalski FC",
        "Saka",
        *(call.title for call in channel.calls),
        *(call.text.splitlines()[0] for call in channel.calls),
    ]
    for text in forbidden:
        assert text not in logged
    assert "alert:2026/27:gw6:digest:120" in logged
    assert "alert:2026/27:gw6:breaking:2" in logged


def test_stop_event_ends_loop_promptly(db):
    stop = threading.Event()
    runtime = alerts_runtime(db, FakeChannel(["msg-1"]), FixedClock(NOW))

    thread = start_alerts(db, runtime, stop)
    assert thread.name == "alerts"
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive()


def test_quiet_breaking_ticks_skip_the_window_query(db, monkeypatch):
    from app.alerts import breaking

    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(60), author="a1")
    clock = FixedClock(DEADLINE_AT - minutes(20))
    channel = FakeChannel(["msg-1"])
    loop = AlertLoop(db, alerts_runtime(db, channel, clock), clock, threading.Event())
    loop.tick()
    add_claim(
        db,
        2,
        SAKA,
        "out",
        created_at=clock.now() - minutes(1),
        author="late2",
        finished=int(minutes(101).total_seconds()),
    )
    clock.advance(timedelta(seconds=5))
    loop.tick()
    assert channel.keys[-1] == "alert:2026/27:gw6:breaking:2"
    real = breaking.listed_players
    window_queries = []

    def counting(*args, **kwargs):
        window_queries.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(breaking, "listed_players", counting)

    for _ in range(3):
        clock.advance(timedelta(seconds=5))
        loop.tick()

    assert window_queries == []
    sent = len(channel.calls)
    add_claim(
        db,
        3,
        SAKA,
        "out",
        created_at=clock.now() - minutes(1),
        author="late3",
        finished=int(minutes(102).total_seconds()),
    )
    clock.advance(timedelta(seconds=5))
    loop.tick()

    assert window_queries == [1]
    assert len(channel.calls) == sent + 1
    assert channel.keys[-1] == "alert:2026/27:gw6:breaking:3"


def calendar_loop(db, at, config=DEFAULT_CONFIG):
    clock = FixedClock(at)
    channel = FakeChannel(["msg-1"])
    runtime = alerts_runtime(db, channel, clock, config=config)
    return AlertLoop(db, runtime, clock, threading.Event()), clock, channel


def digest_minutes(config=DEFAULT_CONFIG):
    return resolve_slots(config.slots, DEADLINE_AT).minutes[0]


def test_default_slots_digest_day_before_news_and_breaking_from_t60(db):
    seed(db)
    n = digest_minutes()
    assert n == 1560
    digest_at = DEADLINE_AT - minutes(n)
    add_claim(db, 1, SAKA, "out", created_at=digest_at - minutes(60), author="a1", finished=-90000)
    loop, clock, channel = calendar_loop(db, digest_at - minutes(1))

    loop.tick()
    assert alert_rows(db) == []

    clock.advance(minutes(1))
    loop.tick()
    rows = alert_rows(db)
    assert [(r.key, r.kind, r.slot_minutes) for r in rows] == [
        (f"alert:2026/27:gw6:digest:{n}", "digest", n)
    ]
    loop.tick()
    assert len(alert_rows(db)) == 1

    add_claim(db, 2, SAKA, "out", created_at=NOW - minutes(180), author="a2", finished=-10800)
    clock.advance(DEADLINE_AT - minutes(60) - clock.now())
    loop.tick()
    assert [r.key for r in alert_rows(db)][-1] == "alert:2026/27:gw6:news:60"
    assert [r.kind for r in alert_rows(db)] == ["digest", "news"]

    add_claim(
        db,
        3,
        SAKA,
        "out",
        created_at=clock.now() - minutes(1),
        author="a3",
        finished=int(minutes(121).total_seconds()),
    )
    clock.advance(minutes(1))
    loop.tick()
    assert [r.key for r in alert_rows(db)][2:] == ["alert:2026/27:gw6:breaking:3"]


def test_restart_after_missed_evening_digest_sends_it_at_once(db):
    seed(db)
    digest_at = DEADLINE_AT - minutes(digest_minutes())
    add_claim(db, 1, SAKA, "out", created_at=digest_at - minutes(60), author="a1", finished=-90000)
    loop, _, _ = calendar_loop(db, digest_at + minutes(13 * 60))

    loop.tick()

    assert [(r.kind, r.status) for r in alert_rows(db)] == [("digest", "sent")]


def test_out_of_order_slot_skipped_with_one_warning(db, caplog):
    seed(db)
    config = AlertConfig((WallClockSlot(1, clock_time(20, 0)), 1600), 3, Decimal("15"), None)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(2000), author="a1", finished=-90000)
    loop, clock, _ = calendar_loop(db, DEADLINE_AT - minutes(1600), config)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            loop.tick()
            clock.advance(minutes(1))

    assert [(r.kind, r.slot_minutes) for r in alert_rows(db)] == [("digest", 1600)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "2026/27:gw6" in warnings[0] and "D-1@20:00" in warnings[0]


def test_a_deadline_moved_after_the_digest_does_not_resend_it(db):
    seed(db)
    digest_at = DEADLINE_AT - minutes(digest_minutes())
    add_claim(db, 1, SAKA, "out", created_at=digest_at - minutes(60), author="a1", finished=-90000)
    loop, clock, channel = calendar_loop(db, digest_at)
    loop.tick()
    moved = DEADLINE_AT + minutes(30)
    with db.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE gameweek SET deadline_at = %(at)s WHERE fpl_id = 6", {"at": moved}
        )
    assert resolve_slots(DEFAULT_CONFIG.slots, moved).minutes[0] == digest_minutes() + 30

    clock.advance(minutes(1))
    loop.tick()
    status = alert_status(db, DEFAULT_CONFIG, clock.now())

    assert [(r.kind, r.slot_minutes) for r in alert_rows(db)] == [("digest", 1560)]
    assert len(channel.calls) == 1
    assert (status.next_slot_kind, status.next_slot_at) == ("news", moved - minutes(60))

    clock.advance(moved - minutes(60) - clock.now())
    loop.tick()
    assert [r.key for r in alert_rows(db)] == [
        "alert:2026/27:gw6:digest:1560",
        "alert:2026/27:gw6:news:60",
    ]
