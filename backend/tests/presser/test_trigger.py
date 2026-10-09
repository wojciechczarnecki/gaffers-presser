import logging

import pytest
from sqlmodel import Session, select

from app.delivery.models import DeliveryLog
from app.delivery.service import DeliveryService
from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.presser.models import Presser
from app.presser.service import (
    PresserFailed,
    PresserRuntime,
    generate_presser,
    presser_key,
    run_after_league_sync,
    send_presser,
)
from app.presser.tracing import NULL_PRESSER_TRACER
from app.presser.writer import PresserDraft, build_writer
from tests.delivery.fakes import FakeChannel, FixedClock
from tests.extraction.fakes import FakeChatModel
from tests.presser.helpers import SEASON, World

MODEL = "openai/gpt-6-luna"
LEAGUES = [100, 200]


def runtime(db, fake, channel, writer=None) -> PresserRuntime:
    clock = FixedClock()
    caller = StructuredCaller(fake, MODEL, load_prices(), clock)
    return PresserRuntime(
        writer=writer or build_writer(caller),
        model=MODEL,
        tracer=NULL_PRESSER_TRACER,
        nicknames={},
        delivery=DeliveryService(db, channel, clock),
        league_ids=LEAGUES,
        clock=clock,
    )


@pytest.fixture
def world(db):
    world = World(db, league_id=100, league_name="Synthetic League A")
    world.add_league(200, "Synthetic League B")
    world.player(1, "Alpha")
    for entry_id, league in [(10, 100), (11, 100), (20, 200), (21, 200)]:
        world.manager(entry_id, f"Manager{entry_id} Surname", league_id=league)
        for gameweek in range(1, 6):
            world.gw(entry_id, gameweek, 40 + entry_id, captain=1)
    return world


def rows(db):
    with Session(db) as session:
        return list(session.exec(select(Presser).order_by(Presser.id)))


def draft(text="Tekst"):
    return PresserDraft(text=text)


def test_sends_one_per_league_latest_gameweek(db, world):
    channel = FakeChannel()
    rt = runtime(db, FakeChatModel(responses=[draft("A"), draft("B")]), channel)
    run_after_league_sync(db, rt, SEASON, 5)
    assert channel.keys == ["presser:2026/27:gw5:league100", "presser:2026/27:gw5:league200"]
    assert [m.title for m in channel.calls] == [
        "Presser GW5 — Synthetic League A",
        "Presser GW5 — Synthetic League B",
    ]
    assert [(r.league_fpl_id, r.status, r.text) for r in rows(db)] == [
        (100, "sent", "A"),
        (200, "sent", "B"),
    ]
    assert all(r.delivery_log_id is not None for r in rows(db))


def test_second_trigger_sends_nothing(db, world):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[draft("A"), draft("B")])
    rt = runtime(db, fake, channel)
    run_after_league_sync(db, rt, SEASON, 5)
    run_after_league_sync(db, rt, SEASON, 5)
    assert len(channel.calls) == 2
    assert len(fake.received_messages) == 2
    assert len(rows(db)) == 2


def test_older_gameweek_sends_nothing(db, world):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[draft()])
    run_after_league_sync(db, runtime(db, fake, channel), SEASON, 4)
    assert channel.calls == [] and fake.received_messages == [] and rows(db) == []


def test_failure_carries_on_and_is_not_retried(db, world):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[ValueError("a"), ValueError("b"), ValueError("c"), draft("B")])
    rt = runtime(db, fake, channel)
    run_after_league_sync(db, rt, SEASON, 5)
    assert [(r.league_fpl_id, r.status) for r in rows(db)] == [(100, "failed"), (200, "sent")]
    assert len(channel.calls) == 1
    calls = len(fake.received_messages)
    run_after_league_sync(db, rt, SEASON, 5)
    assert len(fake.received_messages) == calls
    assert len(channel.calls) == 1


def test_delivery_failure_marks_failed(db, world):
    from app.delivery.channels.base import ChannelRejectedError

    channel = FakeChannel(results=[ChannelRejectedError("no", 400)])
    rt = runtime(db, FakeChatModel(responses=[draft("A"), draft("B")]), channel)
    run_after_league_sync(db, rt, SEASON, 5)
    statuses = [(r.status, r.error_class) for r in rows(db)]
    assert statuses == [("failed", "ChannelRejectedError")] * 2


def test_failed_preview_does_not_stop_the_automatic_presser(db, world, caplog):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[ValueError("a"), ValueError("b"), ValueError("c"), draft("A")])
    rt = runtime(db, fake, channel)
    rt.league_ids = [100]
    with pytest.raises(PresserFailed):
        generate_presser(db, rt, SEASON, 100, 5, preview=True)
    run_after_league_sync(db, rt, SEASON, 5)
    assert [(r.status, r.text) for r in rows(db)] == [("preview_failed", None), ("sent", "A")]
    assert channel.keys == ["presser:2026/27:gw5:league100"]
    with caplog.at_level(logging.INFO):
        run_after_league_sync(db, rt, SEASON, 5)
    assert "presser skipped: gameweek=5 league=#1 already attempted" in caplog.text
    assert "Synthetic" not in caplog.text and "league=100" not in caplog.text


def test_league_without_facts_is_skipped_and_the_next_one_sent(db, world, caplog):
    world.add_league(300, "Synthetic League C")
    channel = FakeChannel()
    fake = FakeChatModel(responses=[draft("A"), draft("C")])
    rt = runtime(db, fake, channel)
    rt.league_ids = [300, 100]
    with caplog.at_level(logging.INFO):
        run_after_league_sync(db, rt, SEASON, 5)
    assert channel.keys == ["presser:2026/27:gw5:league100"]
    assert [(r.league_fpl_id, r.status) for r in rows(db)] == [(100, "sent")]
    assert "presser skipped: gameweek=5 league=#1 error=NoFactsError" in caplog.text


def _seed_sent_delivery(db, key: str) -> int:
    with Session(db) as session:
        row = DeliveryLog(
            idempotency_key=key,
            kind="presser",
            channel="fake",
            title="t",
            text_body="b",
            status="sent",
            attempts=1,
            requested_at=FixedClock().now(),
        )
        session.add(row)
        session.commit()
        assert row.id is not None
        return row.id


def test_delivered_but_unmarked_presser_is_marked_without_a_new_call(db, world):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[draft("A"), draft("again")])
    rt = runtime(db, fake, channel)
    generate_presser(db, rt, SEASON, 100, 5)
    log_id = _seed_sent_delivery(db, presser_key(SEASON, 5, 100))
    status = send_presser(db, rt, SEASON, 100, 5, skip_statuses=("sent",))
    assert status == "already_sent"
    assert len(fake.received_messages) == 1
    assert channel.calls == []
    ((row_status, row_log_id),) = [(r.status, r.delivery_log_id) for r in rows(db)]
    assert (row_status, row_log_id) == ("sent", log_id)
    with Session(db) as session:
        from app.presser.store import previous_pressers

        assert [p.gameweek for p in previous_pressers(session, SEASON, 100, 6)] == [5]


def test_delivery_already_sent_after_generation_leaves_the_row_generated(db, world):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[draft("A")])
    inner = build_writer(StructuredCaller(fake, MODEL, load_prices(), FixedClock()))

    class RacingWriter:
        def run(self, item):
            reply = inner.run(item)
            _seed_sent_delivery(db, presser_key(SEASON, 5, 100))
            return reply

    rt = runtime(db, fake, channel, writer=RacingWriter())
    status = send_presser(db, rt, SEASON, 100, 5, skip_statuses=("sent",))
    assert status == "already_sent"
    assert channel.calls == []
    assert [(r.status, r.delivery_log_id) for r in rows(db)] == [("generated", None)]
