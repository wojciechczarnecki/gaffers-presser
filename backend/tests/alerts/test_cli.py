import os
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlmodel import Session
from typer.testing import CliRunner

from app.alerts.cli import AlertsCliDeps, app
from app.alerts.config import AlertConfig
from app.corroboration.runtime import sql_only_runtime
from app.delivery.models import DeliveryLog
from tests.alerts.helpers import deadline
from tests.alerts.test_store import post, record
from tests.corroboration.helpers import NOW, SAKA, add_claim, seed_reference
from tests.delivery.fakes import FixedClock

CONFIG = AlertConfig((120, 30), 3, Decimal("15"), None)
DEADLINE_AT = NOW + timedelta(hours=2)
REAL = deadline()


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in list(os.environ):
        if re.match(r"(ALERT|LLM_|LANGFUSE_|.*_API_KEY|EMBEDDING_MODEL|USD_PLN_RATE)", name):
            monkeypatch.delenv(name)


def invoke(db, *args, clock=None, config=CONFIG):
    deps = AlertsCliDeps(
        engine=db,
        config=config,
        clock=clock or FixedClock(NOW),
        make_runtime=lambda: sql_only_runtime("test"),
        league_ids_raw="1",
    )
    return CliRunner().invoke(app, list(args), obj=deps)


def seed(db):
    seed_reference(db, {5: NOW - timedelta(days=7), 6: DEADLINE_AT})


def test_status_shows_next_slot_last_alert_and_failures(db):
    seed(db)

    first = invoke(db, "status", clock=FixedClock(NOW - timedelta(hours=3)))
    assert first.exit_code == 0
    assert "Alert deadline: 2026/27:gw6 (real)  2026-09-29 22:00 Europe/Warsaw" in first.stdout
    assert "Next slot: digest 2026-09-29 20:00" in first.stdout
    assert "Last alert: never" in first.stdout
    assert "Failed alerts: 0" in first.stdout

    record(db, "alert:2026/27:gw6:digest:120", status="sent", at=NOW, deadline_value=REAL)
    record(
        db,
        "alert:2026/27:gw6:news:30",
        kind="news",
        slot=30,
        status="failed",
        at=NOW + timedelta(minutes=90),
        deadline_value=REAL,
    )
    later = invoke(db, "status", clock=FixedClock(NOW + timedelta(minutes=100)))
    assert "Next slot: none, breaking until 2026-09-29 22:00" in later.stdout
    assert "Last alert: news 2026-09-29 21:30 failed" in later.stdout
    assert "Failed alerts: 1" in later.stdout


def test_status_without_a_deadline(db):
    result = invoke(db, "status")

    assert result.exit_code == 0
    assert "Alert deadline: none upcoming" in result.stdout


def delivery_row(db, key, accepted_at):
    with Session(db) as session:
        row = DeliveryLog(
            idempotency_key=key,
            kind="alert",
            channel="fake",
            title="t",
            text_body="b",
            status="sent",
            attempts=1,
            requested_at=accepted_at,
            accepted_at=accepted_at,
        )
        session.add(row)
        session.commit()
        return row.id


def latency_post(db, x_id, fetch, extract, created=NOW):
    add_claim(db, x_id, SAKA, created_at=created, author=f"a{x_id}", finished=int(fetch + extract))
    with db.begin() as conn:
        conn.execute(
            text("UPDATE tweet SET first_fetched_at = :at WHERE x_id = :x_id"),
            {"at": created + timedelta(seconds=fetch), "x_id": x_id},
        )


def sent_alert(db, key, x_id, accepted_after, deadline_value, kind="breaking", at=None, extra=()):
    log_id = delivery_row(db, key, NOW + timedelta(seconds=accepted_after))
    from app.alerts.store import record_alert

    record_alert(
        db,
        key=key,
        deadline=deadline_value,
        kind=kind,
        slot_minutes=None,
        trigger_x_id=x_id,
        as_of=at or NOW,
        status="sent",
        delivery_log_id=log_id,
        posts=[post(x_id), *extra],
        recorded_at=at or NOW,
    )


def test_latency_default_gameweek_and_rehearsal(db):
    seed(db)
    rehearsal = deadline(rehearsal=True)
    for x_id, fetch, extract in ((1, 10, 15), (2, 30, 10), (3, 20, 30), (4, 5, 5), (5, 8, 9)):
        latency_post(db, x_id, fetch, extract)
    sent_alert(db, "alert:2026/27:gw6:breaking:1", 1, 31, REAL, at=NOW + timedelta(minutes=1))
    sent_alert(
        db,
        "alert:2026/27:gw6:breaking:2",
        2,
        60,
        REAL,
        at=NOW + timedelta(minutes=2),
        extra=[post(1, freshness="context")],
    )
    sent_alert(db, "alert:2026/27:gw6:breaking:3", 3, 101, REAL, at=NOW + timedelta(minutes=3))
    # a failed alert and a skipped one do not count; neither does another deadline
    record(
        db,
        "alert:2026/27:gw6:breaking:5",
        kind="breaking",
        slot=None,
        status="failed",
        posts=[post(5)],
        at=NOW + timedelta(minutes=4),
        deadline_value=REAL,
    )
    sent_alert(
        db, f"alert:{rehearsal.key}:breaking:4", 4, 20, rehearsal, at=NOW + timedelta(hours=1)
    )

    gw = invoke(db, "latency", "--gameweek", "6")
    assert gw.exit_code == 0, gw.stderr
    assert "Alert deadline: 2026/27:gw6" in gw.stdout
    assert "Posts: 3" in gw.stdout
    lines = {line.split("  ")[0].strip(): line.split() for line in gw.stdout.splitlines()[3:]}
    assert lines["post -> first fetch"][-6:] == ["20.0", "s", "30.0", "s", "30.0", "s"]
    assert lines["fetch -> extraction done"][-6:] == ["15.0", "s", "30.0", "s", "30.0", "s"]
    assert lines["extraction -> accepted"][-6:] == ["20.0", "s", "51.0", "s", "51.0", "s"]
    assert lines["total"][-6:] == ["60.0", "s", "101.0", "s", "101.0", "s"]

    default = invoke(db, "latency")
    assert f"Alert deadline: {rehearsal.key}" in default.stdout
    assert "Posts: 1" in default.stdout

    reh = invoke(db, "latency", "--rehearsal")
    assert f"Alert deadline: {rehearsal.key}" in reh.stdout
    assert "Posts: 1" in reh.stdout

    assert invoke(db, "latency", "--gameweek", "9").exit_code == 1
    both = invoke(db, "latency", "--gameweek", "6", "--rehearsal")
    assert both.exit_code == 1 and "exclude each other" in both.stderr


def test_latency_without_alerts_fails_clearly(db):
    result = invoke(db, "latency")

    assert result.exit_code == 1
    assert "no alerts recorded" in result.stderr


def test_cli_rejects_invalid_alert_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALERT_SLOTS_MINUTES", "30,120")

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert "ALERT_SLOTS_MINUTES" in result.stderr
