from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from decimal import Decimal

from app.alerts.config import AlertConfig, WallClockSlot
from app.alerts.schedule import resolve_slots
from app.alerts.status import alert_status, status_line
from tests.alerts.helpers import deadline
from tests.alerts.test_store import record
from tests.corroboration.helpers import SAKA, add_claim, seed_reference

DEADLINE_AT = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
CONFIG = AlertConfig((120, 30), 3, Decimal("15"), None)
REAL = deadline()


def fmt(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def seed(db):
    seed_reference(db, {5: DEADLINE_AT - timedelta(days=7), 6: DEADLINE_AT})
    add_claim(db, 1, SAKA)


def test_alert_status_next_slot_last_alert_and_failures(db):
    seed(db)
    now = DEADLINE_AT - timedelta(hours=5)

    first = alert_status(db, CONFIG, now)
    assert first.deadline is not None and first.deadline.key == "2026/27:gw6"
    assert (first.next_slot_kind, first.next_slot_at) == (
        "digest",
        DEADLINE_AT - timedelta(hours=2),
    )
    assert first.breaking_until is None and first.last is None and first.failed == 0
    assert (
        status_line(first, fmt) == "Alerts: next slot: digest 14:00  last alert: never  failed: 0"
    )

    sent_at = DEADLINE_AT - timedelta(hours=2)
    record(db, "alert:2026/27:gw6:digest:120", status="sent", at=sent_at, posts=[])
    after_digest = alert_status(db, CONFIG, sent_at + timedelta(minutes=1))
    assert (after_digest.next_slot_kind, after_digest.next_slot_at) == (
        "news",
        DEADLINE_AT - timedelta(minutes=30),
    )
    assert after_digest.last is not None and after_digest.last.kind == "digest"

    news_at = DEADLINE_AT - timedelta(minutes=30)
    record(
        db, "alert:2026/27:gw6:news:30", kind="news", slot=30, status="failed", at=news_at, posts=[]
    )
    breaking = alert_status(db, CONFIG, news_at + timedelta(minutes=1))
    assert breaking.next_slot_at is None and breaking.breaking_until == DEADLINE_AT
    assert (breaking.last.kind, breaking.last.status, breaking.failed) == ("news", "failed", 1)
    assert status_line(breaking, fmt) == (
        "Alerts: breaking until 16:00  last alert: news 15:30 failed  failed: 1"
    )


def test_alert_status_without_an_upcoming_deadline(db):
    status = alert_status(db, CONFIG, DEADLINE_AT)

    assert status.deadline is None
    assert status_line(status, fmt) == "Alerts: no upcoming deadline  last alert: never  failed: 0"


def test_alert_status_follows_a_rehearsal_deadline(db):
    seed(db)
    rehearsal = DEADLINE_AT - timedelta(days=2)
    config = AlertConfig((120, 30), 3, Decimal("15"), rehearsal)

    status = alert_status(db, config, rehearsal - timedelta(hours=3))

    assert status.deadline is not None and status.deadline.rehearsal
    assert status.next_slot_at == rehearsal - timedelta(hours=2)


def test_status_with_wall_clock_digest(db):
    seed(db)
    config = AlertConfig((WallClockSlot(1, clock_time(20, 0)), 60), 3, Decimal("15"), None)
    n = resolve_slots(config.slots, DEADLINE_AT).minutes[0]
    digest_at = DEADLINE_AT - timedelta(minutes=n)
    now = digest_at - timedelta(hours=1)

    first = alert_status(db, config, now)
    assert (first.next_slot_kind, first.next_slot_at) == ("digest", digest_at)

    record(db, f"alert:2026/27:gw6:digest:{n}", slot=n, status="sent", at=digest_at, posts=[])
    after = alert_status(db, config, digest_at + timedelta(minutes=1))
    assert (after.next_slot_kind, after.next_slot_at) == (
        "news",
        DEADLINE_AT - timedelta(minutes=60),
    )

    news_at = DEADLINE_AT - timedelta(minutes=60)
    record(
        db, "alert:2026/27:gw6:news:60", kind="news", slot=60, status="sent", at=news_at, posts=[]
    )
    breaking = alert_status(db, config, news_at + timedelta(minutes=1))
    assert breaking.next_slot_at is None and breaking.breaking_until == DEADLINE_AT
