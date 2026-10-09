import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.delivery.service import DeliveryService
from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.presser.models import Presser
from app.presser.service import PresserRuntime, run_after_league_sync
from app.presser.tracing import NULL_PRESSER_TRACER
from app.presser.writer import PresserDraft, build_writer
from app.worker.loop import Worker
from tests.delivery.fakes import FakeChannel
from tests.delivery.fakes import FixedClock as DeliveryClock
from tests.extraction.fakes import FakeChatModel
from tests.worker.sim import ENTRY_IDS, LEAGUE_ID, FakeClock, SimulatedFpl, run_until

MODEL = "openai/gpt-6-luna"
SEASON = "2026/27"
MARKER = "FAKE-PRESSER-MARKER"
START = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
NICKNAME = "Bartasik"


def build_runtime(db, fake, channel, nicknames=None) -> PresserRuntime:
    clock = DeliveryClock()
    return PresserRuntime(
        writer=build_writer(StructuredCaller(fake, MODEL, load_prices(), clock)),
        model=MODEL,
        tracer=NULL_PRESSER_TRACER,
        nicknames=nicknames or {},
        delivery=DeliveryService(db, channel, clock),
        league_ids=[LEAGUE_ID],
        clock=clock,
    )


def run_worker(db, runtime, minutes=70, start=START) -> None:
    clock = FakeClock(start, start + timedelta(minutes=minutes))
    worker = Worker(
        db,
        SimulatedFpl(clock).client(),
        [LEAGUE_ID],
        clock,
        after_league_sync=lambda season, gameweek: run_after_league_sync(
            db, runtime, season, gameweek
        ),
    )
    run_until(worker)


def test_catch_up_sends_only_latest_gameweek_and_restart_sends_nothing(db):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[PresserDraft(text=f"Tekst {MARKER}")])
    runtime = build_runtime(db, fake, channel)
    run_worker(db, runtime)

    assert channel.keys == [f"presser:{SEASON}:gw5:league{LEAGUE_ID}"]
    assert len(fake.received_messages) == 1
    with Session(db) as session:
        rows = session.exec(select(Presser)).all()
    assert [(r.gameweek_fpl_id, r.status) for r in rows] == [(5, "sent")]

    second_channel = FakeChannel()
    second_fake = FakeChatModel(responses=[])
    run_worker(
        db,
        build_runtime(db, second_fake, second_channel),
        minutes=20,
        start=START + timedelta(minutes=90),
    )
    assert second_channel.calls == []
    assert second_fake.received_messages == []


def test_logs_carry_no_names_or_text(db, caplog):
    channel = FakeChannel()
    fake = FakeChatModel(responses=[PresserDraft(text=f"Tekst {MARKER}")])
    runtime = build_runtime(db, fake, channel, nicknames={ENTRY_IDS[0]: NICKNAME})
    with caplog.at_level(logging.DEBUG):
        run_worker(db, runtime)

    assert channel.calls
    assert "presser sent: gameweek=5 league=#1" in caplog.text
    assert "Synthetic" not in caplog.text
    assert NICKNAME not in caplog.text
    assert MARKER not in caplog.text
    assert str(LEAGUE_ID) not in caplog.text
    for entry_id in ENTRY_IDS:
        assert str(entry_id) not in caplog.text
    sheet_text = fake.received_messages[0][1].content
    assert NICKNAME in sheet_text
