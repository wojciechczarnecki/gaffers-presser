import json
import os
import re

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.delivery.models import DeliveryLog
from app.delivery.service import DeliveryService
from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.presser.cli import PresserCliDeps, app
from app.presser.config import PresserSettings
from app.presser.models import Presser
from app.presser.service import PresserRuntime
from app.presser.tracing import NULL_PRESSER_TRACER
from app.presser.writer import PresserDraft, build_writer
from tests.delivery.fakes import FakeChannel, FixedClock
from tests.extraction.fakes import FakeChatModel
from tests.presser.helpers import LEAGUE_ID, SEASON, World

MODEL = "openai/gpt-6-luna"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in list(os.environ):
        if re.match(r"(PRESSER_|.*_API_KEY|LANGFUSE_|DELIVERY_)", name):
            monkeypatch.delenv(name)


@pytest.fixture
def world(db):
    world = World(db)
    world.player(1, "Alpha")
    for entry_id, name in [(10, "Anna Alfa"), (11, "Bartek Beta")]:
        world.manager(entry_id, name)
        for gameweek in range(1, 6):
            world.gw(entry_id, gameweek, 50 + entry_id, captain=1)
    return world


class Harness:
    def __init__(self, db, responses=(), key=True, delivery=True, enabled="true", nicknames=None):
        self.fake = FakeChatModel(responses=list(responses))
        self.channel = FakeChannel()
        self.clock = FixedClock()
        self.runtime_requests: list[bool] = []
        self.db = db
        self.deps = PresserCliDeps(
            engine=db,
            settings=PresserSettings(
                _env_file=None,
                presser_enabled=enabled,
                openrouter_api_key="sk-test" if key else None,
            ),
            nicknames=nicknames or {},
            make_runtime=self.make_runtime,
            clock=self.clock,
            league_ids_raw=str(LEAGUE_ID),
            delivery_enabled=delivery,
        )

    def make_runtime(self, with_delivery: bool) -> PresserRuntime:
        self.runtime_requests.append(with_delivery)
        return PresserRuntime(
            writer=build_writer(StructuredCaller(self.fake, MODEL, load_prices(), self.clock)),
            model=MODEL,
            tracer=NULL_PRESSER_TRACER,
            nicknames=self.deps.nicknames,
            delivery=DeliveryService(self.db, self.channel, self.clock) if with_delivery else None,
            league_ids=[LEAGUE_ID],
            clock=self.clock,
        )

    def invoke(self, *args):
        return CliRunner().invoke(app, list(args), obj=self.deps)


def rows(db):
    with Session(db) as session:
        return list(session.exec(select(Presser).order_by(Presser.id)))


def test_facts_prints_sheet(db, world):
    result = Harness(db).invoke("facts", "--league", str(LEAGUE_ID), "--gameweek", "5")
    assert result.exit_code == 0
    sheet = json.loads(result.stdout)
    assert [w["manager"] for w in sheet["winners"]] == ["Bartek"]
    assert sheet["gameweek"] == 5


def test_facts_with_a_nickname(db, world):
    result = Harness(db, nicknames={11: "Bartas"}).invoke(
        "facts", "--league", str(LEAGUE_ID), "--gameweek", "5"
    )
    assert json.loads(result.stdout)["winners"][0]["manager"] == "Bartas"


def test_facts_for_a_gameweek_without_data_fails(db, world):
    result = Harness(db).invoke("facts", "--league", str(LEAGUE_ID), "--gameweek", "9")
    assert result.exit_code == 1
    assert "NoFactsError" in result.stderr


def test_preview_neither_sends_nor_marks_sent(db, world):
    harness = Harness(db, [PresserDraft(text="Tekst presera")])
    result = harness.invoke("preview", "--league", str(LEAGUE_ID), "--gameweek", "5")
    assert result.exit_code == 0
    assert "Tekst presera" in result.stdout
    assert re.search(r"chars=13 cost=\$0\.\d{4} latency=\d+\.\ds", result.stdout)
    assert harness.channel.calls == []
    assert harness.runtime_requests == [False]
    assert [r.status for r in rows(db)] == ["generated"]
    with Session(db) as session:
        assert session.exec(select(DeliveryLog)).all() == []


def test_send_older_gameweek_then_already_sent(db, world):
    harness = Harness(db, [PresserDraft(text="Tekst GW3")])
    first = harness.invoke("send", "--league", str(LEAGUE_ID), "--gameweek", "3")
    assert first.exit_code == 0
    assert first.stdout.strip() == "sent"
    assert harness.channel.keys == [f"presser:{SEASON}:gw3:league{LEAGUE_ID}"]
    second = harness.invoke("send", "--league", str(LEAGUE_ID), "--gameweek", "3")
    assert second.exit_code == 0
    assert second.stdout.strip() == "already_sent"
    assert len(harness.fake.received_messages) == 1
    assert len(harness.channel.calls) == 1


def test_send_failure_exits_1(db, world):
    from app.delivery.channels.base import ChannelRejectedError

    harness = Harness(db, [PresserDraft(text="Tekst")])
    harness.channel = FakeChannel(results=[ChannelRejectedError("no", 400)])
    result = harness.invoke("send", "--league", str(LEAGUE_ID), "--gameweek", "3")
    assert result.exit_code == 1
    assert result.stdout.strip() == "failed"


def test_preview_and_send_fail_with_the_disabled_reason(db, world):
    harness = Harness(db, key=False)
    for command in ("preview", "send"):
        result = harness.invoke(command, "--league", str(LEAGUE_ID), "--gameweek", "5")
        assert result.exit_code == 1
        assert "OPENROUTER_API_KEY is not set" in result.stderr
    no_delivery = Harness(db, delivery=False).invoke(
        "send", "--league", str(LEAGUE_ID), "--gameweek", "5"
    )
    assert no_delivery.exit_code == 1
    assert "delivery disabled" in no_delivery.stderr


def test_status_says_why_disabled(db, world):
    assert "Presser: disabled (OPENROUTER_API_KEY is not set)" in (
        Harness(db, key=False).invoke("status").stdout
    )
    assert "Presser: disabled (PRESSER_ENABLED=false)" in (
        Harness(db, enabled="false").invoke("status").stdout
    )
    assert "Presser: disabled (delivery disabled)" in (
        Harness(db, delivery=False).invoke("status").stdout
    )


def test_status_shows_latest_presser_and_failures(db, world):
    harness = Harness(
        db, [PresserDraft(text="Tekst"), ValueError("x"), ValueError("x"), ValueError("x")]
    )
    harness.invoke("send", "--league", str(LEAGUE_ID), "--gameweek", "4")
    harness.invoke("send", "--league", str(LEAGUE_ID), "--gameweek", "5")
    result = harness.invoke("status")
    assert f"Presser: enabled (model={MODEL})" in result.stdout
    assert f"League {LEAGUE_ID}: GW5 failed" in result.stdout
    assert "Europe/Warsaw" in result.stdout
    assert "failed: 1" in result.stdout


def test_status_without_a_presser(db, world):
    result = Harness(db).invoke("status")
    assert f"League {LEAGUE_ID}: no presser yet" in result.stdout


def test_invalid_nicknames_stops_cli(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRESSER_NICKNAMES", "not json {secret}")
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 1
    assert "PRESSER_NICKNAMES" in result.stderr
    assert "not json" not in result.stderr and "secret" not in result.stderr


def test_help_lists_the_commands():
    result = CliRunner().invoke(app, ["--help"])
    for command in ("facts", "preview", "send", "status"):
        assert command in result.stdout
