import os
import re
from datetime import time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlmodel import Session
from typer.testing import CliRunner

from app.alerts.cli import AlertsCliDeps, app
from app.alerts.config import AlertConfig, WallClockSlot
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
    assert "Next slot: digest Tue 2026-09-29 20:00" in first.stdout
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
    assert "Next slot: none, breaking until Tue 2026-09-29 22:00" in later.stdout
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
    lines = {line.split("  ")[0].strip(): line.split() for line in gw.stdout.splitlines()[3:7]}
    assert lines["post -> first fetch"][-6:] == ["20.0", "s", "30.0", "s", "30.0", "s"]
    assert lines["fetch -> extraction done"][-6:] == ["15.0", "s", "30.0", "s", "30.0", "s"]
    assert lines["extraction -> accepted"][-6:] == ["20.0", "s", "51.0", "s", "51.0", "s"]
    assert lines["total"][-6:] == ["60.0", "s", "101.0", "s", "101.0", "s"]
    assert "Kind: breaking  posts: 3" in gw.stdout
    assert "Kind: digest" not in gw.stdout

    default = invoke(db, "latency")
    assert f"Alert deadline: {rehearsal.key}" in default.stdout
    assert "Posts: 1" in default.stdout

    reh = invoke(db, "latency", "--rehearsal")
    assert f"Alert deadline: {rehearsal.key}" in reh.stdout
    assert "Posts: 1" in reh.stdout

    assert invoke(db, "latency", "--gameweek", "9").exit_code == 1
    both = invoke(db, "latency", "--gameweek", "6", "--rehearsal")
    assert both.exit_code == 1 and "exclude each other" in both.stderr


def test_latency_reports_digest_and_breaking_posts_separately(db):
    seed(db)
    latency_post(db, 1, 600, 300)
    latency_post(db, 2, 10, 15)
    sent_alert(db, "alert:2026/27:gw6:digest:120", 1, 8100, REAL, kind="digest")
    sent_alert(db, "alert:2026/27:gw6:breaking:2", 2, 31, REAL, at=NOW + timedelta(minutes=1))

    result = invoke(db, "latency", "--gameweek", "6")

    assert result.exit_code == 0, result.stderr
    out = result.stdout.splitlines()
    assert "Posts: 2" in out
    digest_at = out.index("Kind: digest  posts: 1")
    breaking_at = out.index("Kind: breaking  posts: 1")
    assert out[digest_at + 5].split()[-6:] == ["8100.0", "s", "8100.0", "s", "8100.0", "s"]
    assert out[breaking_at + 5].split()[-6:] == ["31.0", "s", "31.0", "s", "31.0", "s"]


def test_latency_without_alerts_fails_clearly(db):
    result = invoke(db, "latency")

    assert result.exit_code == 1
    assert "no alerts recorded" in result.stderr


def test_cli_rejects_invalid_alert_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALERT_SLOTS", "30,120")

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert "ALERT_SLOTS" in result.stderr


def test_cli_rejects_retired_slots_minutes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALERT_SLOTS_MINUTES", "120,30")

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert "ALERT_SLOTS_MINUTES" in result.stderr and "ALERT_SLOTS" in result.stderr


def row_counts(db) -> dict[str, int]:
    with db.connect() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).scalars()
        return {
            name: conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
            for name in tables
        }


def preview_world(db):
    from tests.alerts.helpers import seed_league

    seed(db)
    seed_league(db, 1, {10: ("Jan Kowalski", "Kowalski FC")}, {10: {5: [SAKA]}})
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")


def test_preview_prints_and_writes_nothing(db):
    from app.alerts.render import load_template

    preview_world(db)
    template = load_template()
    before = row_counts(db)

    digest = invoke(db, "preview", "--at", "2026-09-29 20:00")

    assert digest.exit_code == 0, digest.stderr
    assert "Alert deadline: 2026/27:gw6" in digest.stdout
    assert "Title: " in digest.stdout
    assert "Saka" in digest.stdout and "Jan Kowalski" not in digest.stdout
    assert "https://x.com/a1/status/1" in digest.stdout
    assert template["sources"]["new_marker"] not in digest.stdout

    # the digest is already in the log; the news at a later moment has nothing new
    from app.alerts.store import record_alert
    from tests.alerts.helpers import deadline as alert_deadline

    real = alert_deadline()
    record_alert(
        db,
        key="alert:2026/27:gw6:digest:120",
        deadline=real.__class__(real.key, DEADLINE_AT, False, "2026/27", 6),
        kind="digest",
        slot_minutes=120,
        trigger_x_id=None,
        as_of=NOW,
        status="sent",
        delivery_log_id=None,
        posts=[post(1)],
        recorded_at=NOW,
    )
    after_digest = row_counts(db)
    skipped = invoke(db, "preview", "--at", "2026-09-29T21:30", "--kind", "news")
    assert skipped.exit_code == 0
    assert "would be skipped" in skipped.stdout

    add_claim(db, 2, SAKA, "out", created_at=NOW + timedelta(minutes=30), author="a2")
    after_claim = row_counts(db)
    news = invoke(db, "preview", "--at", "2026-09-29T21:30", "--kind", "news")
    assert news.exit_code == 0
    assert "status/2" in news.stdout
    marked = [
        line for line in news.stdout.splitlines() if template["sources"]["new_marker"] in line
    ]
    assert len(marked) == 1 and "status/2" in marked[0]

    assert before["alert"] == 0 and before["delivery_log"] == 0
    assert row_counts(db)["alert"] == after_digest["alert"] == 1
    assert row_counts(db) == after_claim


def test_preview_rejects_bad_input(db):
    preview_world(db)

    bad_time = invoke(db, "preview", "--at", "tomorrow")
    assert bad_time.exit_code == 1 and "2026-10-04T18:00" in bad_time.stderr

    bad_kind = invoke(db, "preview", "--at", "2026-09-29 20:00", "--kind", "breaking")
    assert bad_kind.exit_code == 1 and "digest or news" in bad_kind.stderr

    past_deadline = invoke(db, "preview", "--at", "2026-09-30 20:00")
    assert past_deadline.exit_code == 1 and "no deadline" in past_deadline.stderr

    no_news = invoke(
        db,
        "preview",
        "--at",
        "2026-09-29 20:00",
        "--kind",
        "news",
        config=AlertConfig((120,), 3, Decimal("15"), None),
    )
    assert no_news.exit_code == 1 and "ALERT_SLOTS has no news slot" in no_news.stderr


def test_preview_with_wall_clock_slots(db):
    preview_world(db)
    calendar = AlertConfig((WallClockSlot(1, time(20, 0)), 60), 3, Decimal("15"), None)
    # D-1@20:00 is 1560 minutes before this deadline, so a 1600 slot leaves it out of order
    clashing = AlertConfig((WallClockSlot(1, time(20, 0)), 1600), 3, Decimal("15"), None)

    digest = invoke(db, "preview", "--at", "2026-09-29 20:00", config=calendar)
    news = invoke(db, "preview", "--at", "2026-09-29 20:00", "--kind", "news", config=calendar)
    skipped = invoke(db, "preview", "--at", "2026-09-29 20:00", "--kind", "news", config=clashing)

    assert digest.exit_code == 0, digest.stderr
    assert "Alert deadline: 2026/27:gw6" in digest.stdout and "Title: " in digest.stdout
    assert news.exit_code == 0, news.stderr
    assert "Title: " in news.stdout
    assert skipped.exit_code == 1
    assert "ALERT_SLOTS has no news slot for that deadline" in skipped.stderr


def test_preview_writes_the_html_part_when_asked(db, tmp_path):
    preview_world(db)
    target = tmp_path / "digest.html"

    result = invoke(db, "preview", "--at", "2026-09-29 20:00", "--html", str(target))

    assert result.exit_code == 0, result.stderr
    html = target.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "Saka" in html and "https://x.com/a1/status/1" in html
