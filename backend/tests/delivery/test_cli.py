import os
import re
import subprocess
import sys
from datetime import timedelta

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.delivery.channels.base import ChannelRejectedError
from app.delivery.cli import DeliveryCliDeps, app
from app.delivery.config import DeliveryConfig, DeliverySettings
from app.delivery.models import DeliveryLog
from tests.conftest import BACKEND_DIR
from tests.delivery.fakes import START, FakeChannel, FixedClock

VARIABLES = re.compile(r"(DELIVERY_.*|RESEND_.*)")

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if VARIABLES.fullmatch(name):
            monkeypatch.delenv(name)


def deps(db, channel=None, provider: str | None = "fake") -> DeliveryCliDeps:
    channel = channel or FakeChannel(["provider-abc"])
    config = (
        None if provider is None else DeliveryConfig(provider, DeliverySettings(_env_file=None))
    )
    return DeliveryCliDeps(
        engine=db, config=config, make_channel=lambda: channel, clock=FixedClock()
    )


def log_rows(engine) -> list[DeliveryLog]:
    with Session(engine) as session:
        return list(session.exec(select(DeliveryLog).order_by(DeliveryLog.id)).all())


def test_send_test_prints_outcome_and_provider_id(db):
    channel = FakeChannel(["provider-abc"])
    result = runner.invoke(app, ["send-test"], obj=deps(db, channel))

    assert result.exit_code == 0, result.output
    assert "status: sent" in result.output
    assert "provider id: provider-abc" in result.output
    (row,) = log_rows(db)
    assert row.kind == "test"
    assert row.idempotency_key.startswith("test:")
    assert row.status == "sent"
    assert "2026-10-01 14:00" in channel.calls[0].text

    again = runner.invoke(app, ["send-test"], obj=deps(db, channel))
    assert again.exit_code == 0
    keys = [row.idempotency_key for row in log_rows(db)]
    assert len(keys) == 2 and len(set(keys)) == 2


def test_send_test_failed_exits_non_zero(db):
    channel = FakeChannel([ChannelRejectedError("no", 403)])
    result = runner.invoke(app, ["send-test"], obj=deps(db, channel))
    assert result.exit_code == 1
    assert "status: failed" in result.output
    assert "ChannelRejectedError" in result.output


def test_send_test_disabled_exits_non_zero(db):
    result = runner.invoke(app, ["send-test"], obj=deps(db, provider=None))
    assert result.exit_code == 1
    assert "delivery is disabled" in result.output
    assert log_rows(db) == []


def seed_rows(engine, count: int) -> None:
    with Session(engine) as session:
        for index in range(count):
            session.add(
                DeliveryLog(
                    idempotency_key=f"alert:{index}",
                    kind="alert" if index % 2 else "presser",
                    channel="fake",
                    title=f"Sekretny tytul {index}",
                    text_body=f"Sekretna tresc {index}",
                    status="sent" if index % 3 else "failed",
                    provider_message_id=f"prov-{index}" if index % 3 else None,
                    attempts=1 + index % 3,
                    requested_at=START + timedelta(minutes=index),
                )
            )
        session.commit()


def test_status_prints_channel_and_last_rows(db):
    seed_rows(db, 12)
    result = runner.invoke(app, ["status"], obj=deps(db))

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == "channel: fake"
    assert len(lines) == 11
    assert lines[1] == "2026-10-01T12:11:00Z  alert  sent  attempts=3  provider_id=prov-11"
    assert lines[2].startswith("2026-10-01T12:10:00Z  presser  sent  attempts=2")
    assert lines[9] == "2026-10-01T12:03:00Z  alert  failed  attempts=1  provider_id=-"
    assert lines[-1].startswith("2026-10-01T12:02:00Z")
    assert "Sekretny" not in result.output and "Sekretna" not in result.output


def test_status_disabled_and_empty_log(db):
    result = runner.invoke(app, ["status"], obj=deps(db, provider=None))
    assert result.exit_code == 0
    assert result.output.splitlines()[0] == "channel: disabled"
    assert "no sends yet" in result.output


def test_bad_provider_fails_on_start(monkeypatch):
    monkeypatch.setenv("DELIVERY_PROVIDER", "smtp")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "DELIVERY_PROVIDER" in result.output


def test_module_entry_point_runs():
    env = {k: v for k, v in os.environ.items() if not VARIABLES.fullmatch(k)}
    result = subprocess.run(
        [sys.executable, "-m", "app.delivery", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=BACKEND_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert "send-test" in result.stdout
