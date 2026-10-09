import logging
import threading

import pytest
from sqlmodel import Session, select

from app.llm.pricing import compute_cost, load_prices
from app.llm.structured import StructuredCaller
from app.presser.models import Presser
from app.presser.service import (
    PresserFailed,
    PresserRuntime,
    PresserStopped,
    generate_presser,
    presser_key,
)
from app.presser.store import insert_presser, previous_pressers
from app.presser.tracing import NULL_PRESSER_TRACER
from app.presser.writer import PresserDraft, build_writer
from tests.delivery.fakes import FixedClock
from tests.extraction.fakes import FakeChatModel
from tests.presser.helpers import LEAGUE_ID, SEASON, World

MODEL = "openai/gpt-6-luna"
MARKER = "FAKE-PRESSER-MARKER"


def runtime(*responses, nicknames=None, league_ids=None) -> tuple[PresserRuntime, FakeChatModel]:
    fake = FakeChatModel(responses=list(responses))
    clock = FixedClock()
    stop_event = threading.Event()
    caller = StructuredCaller(fake, MODEL, load_prices(), clock, stop_event=stop_event)
    return (
        PresserRuntime(
            writer=build_writer(caller),
            model=MODEL,
            tracer=NULL_PRESSER_TRACER,
            nicknames=nicknames or {},
            delivery=None,
            league_ids=league_ids or [LEAGUE_ID],
            stop_event=stop_event,
            clock=clock,
        ),
        fake,
    )


@pytest.fixture
def world(db):
    world = World(db)
    world.player(1, "Alpha")
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta")]:
        world.manager(entry_id, name)
        for gameweek in range(1, 6):
            world.gw(entry_id, gameweek, 50 + entry_id, captain=1)
    return world


def rows(db) -> list[Presser]:
    with Session(db) as session:
        return list(session.exec(select(Presser).order_by(Presser.id)))


def test_generated_presser_stored_with_usage(db, world):
    rt, _ = runtime(PresserDraft(text=f"Tekst {MARKER}"))
    result = generate_presser(db, rt, SEASON, LEAGUE_ID, 5)
    (row,) = rows(db)
    assert row.id == result.id
    assert row.text == f"Tekst {MARKER}"
    assert row.model == MODEL
    assert row.status == "generated"
    assert (row.input_tokens, row.output_tokens) == (10, 5)
    assert row.cost_usd == pytest.approx(compute_cost(MODEL, 10, 5, load_prices()))
    assert row.latency_seconds is not None and row.latency_seconds >= 0
    assert row.idempotency_key == presser_key(SEASON, 5, LEAGUE_ID)
    assert row.facts["gameweek"] == 5
    assert row.facts["winners"][0]["manager"] == "Bartek"
    assert row.prompt_version.startswith("presser_writer@")


def test_previous_pressers_latest_two_sent_before_gameweek(db, world):
    def add(gameweek, status, text):
        insert_presser(
            db,
            season=SEASON,
            league_fpl_id=LEAGUE_ID,
            gameweek_fpl_id=gameweek,
            idempotency_key=presser_key(SEASON, gameweek, LEAGUE_ID),
            facts={},
            text=text,
            model=MODEL,
            prompt_version="presser_writer@1",
            status=status,
            created_at=FixedClock().now(),
        )

    add(1, "sent", "one")
    add(2, "sent", "two")
    add(2, "generated", "two preview")
    add(3, "sent", "three")
    add(4, "failed", None)
    add(5, "sent", "five")
    with Session(db) as session:
        assert [(p.gameweek, p.text) for p in previous_pressers(session, SEASON, LEAGUE_ID, 5)] == [
            (2, "two"),
            (3, "three"),
        ]
        assert previous_pressers(session, SEASON, LEAGUE_ID, 1) == []
        assert [p.gameweek for p in previous_pressers(session, SEASON, LEAGUE_ID, 2)] == [1]

    rt, fake = runtime(PresserDraft(text="x"))
    generate_presser(db, rt, SEASON, LEAGUE_ID, 5)
    human = fake.received_messages[0][1].content
    assert "GW2:\ntwo" in human and "GW3:\nthree" in human and "GW1:" not in human


def test_writer_failure_recorded_failed_and_logged_by_class(db, world, caplog):
    rt, _ = runtime(ValueError("boom Bartek"), ValueError("boom"), ValueError("boom"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PresserFailed):
            generate_presser(db, rt, SEASON, LEAGUE_ID, 5)
    (row,) = rows(db)
    assert (row.status, row.error_class, row.text) == ("failed", "ValueError", None)
    assert "ValueError" in caplog.text
    for forbidden in ("Bartek", "Anna", "Synthetic", str(LEAGUE_ID), "boom"):
        assert forbidden not in caplog.text
    assert "league=#1" in caplog.text
    assert rt.clock.sleeps == [2.0, 4.0]


def test_stop_records_nothing(db, world):
    rt, fake = runtime(ValueError("a"), ValueError("b"), ValueError("c"))
    rt.stop_event.set()
    with pytest.raises(PresserStopped):
        generate_presser(db, rt, SEASON, LEAGUE_ID, 5)
    assert rows(db) == []
    assert fake.received_messages == []
