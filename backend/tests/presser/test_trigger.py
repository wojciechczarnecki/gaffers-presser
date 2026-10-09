import pytest
from sqlmodel import Session, select

from app.delivery.service import DeliveryService
from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.presser.models import Presser
from app.presser.service import PresserRuntime, run_after_league_sync
from app.presser.tracing import NULL_PRESSER_TRACER
from app.presser.writer import PresserDraft, build_writer
from tests.delivery.fakes import FakeChannel, FixedClock
from tests.extraction.fakes import FakeChatModel
from tests.presser.helpers import SEASON, World

MODEL = "openai/gpt-6-luna"
LEAGUES = [100, 200]


def runtime(db, fake, channel) -> PresserRuntime:
    clock = FixedClock()
    caller = StructuredCaller(fake, MODEL, load_prices(), clock)
    return PresserRuntime(
        writer=build_writer(caller),
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
