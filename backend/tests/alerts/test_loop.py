import contextlib
import logging
import threading
from datetime import timedelta

from sqlmodel import Session, select

from app.alerts import loop as loop_module
from app.alerts.loop import AlertLoop, start_alerts
from app.alerts.models import Alert
from app.delivery.models import DeliveryLog
from app.worker.jobs import Shutdown
from tests.alerts.helpers import alerts_runtime
from tests.alerts.test_slots import DEADLINE_AT, seed
from tests.corroboration.helpers import NOW, SAKA, add_claim
from tests.delivery.fakes import FakeChannel, FixedClock


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
