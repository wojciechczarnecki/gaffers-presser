from datetime import timedelta

from sqlmodel import Session, select

from app.alerts.models import Alert, AlertPost
from app.alerts.schemas import AlertDeadline
from app.alerts.store import (
    IncludedPost,
    alert_exists,
    done_slots,
    included_origins,
    record_alert,
    status_rows,
)
from tests.alerts.helpers import DEADLINE_AT, SEASON, deadline, set_raw
from tests.corroboration.helpers import ISAK, NOW, SAKA, add_claim, seed_reference

REAL = deadline()
OTHER = AlertDeadline("2026/27:gw7", DEADLINE_AT + timedelta(days=7), False, SEASON, 7)


def post(x_id, player=SAKA, freshness="new"):
    return IncludedPost(SEASON, player, x_id, freshness)


def record(
    db,
    key,
    kind="digest",
    slot=120,
    status="sent",
    posts=(),
    at=NOW,
    deadline_value=REAL,
    trigger=None,
):
    return record_alert(
        db,
        key=key,
        deadline=deadline_value,
        kind=kind,
        slot_minutes=slot,
        trigger_x_id=trigger,
        as_of=at,
        status=status,
        delivery_log_id=None,
        posts=list(posts),
        recorded_at=at,
    )


def seed(db):
    seed_reference(db)
    for x_id in (1, 2, 3, 4, 5):
        add_claim(db, x_id, SAKA)


def test_included_is_scoped_to_the_deadline_key(db):
    seed(db)
    record(db, "k1", posts=[post(1)])
    record(db, "k2", kind="news", slot=30, status="failed", posts=[post(2)], at=NOW + timedelta(1))
    record(db, "k3", kind="news", slot=10, status="skipped", at=NOW + timedelta(2))
    record(db, "k4", posts=[post(3)], deadline_value=OTHER)

    with Session(db) as session:
        assert included_origins(session, REAL.key) == {1, 2}
        assert included_origins(session, OTHER.key) == {3}
        assert included_origins(session, "rehearsal:2026-10-04T16:00Z") == set()
        assert included_origins(session, REAL.key, before=NOW + timedelta(seconds=1)) == {1}


def test_included_origins_cover_reposts(db):
    seed(db)
    set_raw(db, 4, {"retweeted_tweet": {"id": 5}})
    record(db, "k1", posts=[post(4)])

    with Session(db) as session:
        assert included_origins(session, REAL.key) == {4, 5}


def test_breaking_context_rows_are_not_included(db):
    seed(db)
    record(
        db,
        "alert:b",
        kind="breaking",
        slot=None,
        posts=[post(1, freshness="new"), post(2, freshness="context"), post(3, ISAK, "context")],
        trigger=1,
    )
    record(db, "d", posts=[post(4, freshness="context"), post(5, freshness="new")])

    with Session(db) as session:
        assert included_origins(session, REAL.key) == {1, 4, 5}


def test_record_is_unique_per_key(db):
    seed(db)
    first = record(db, "same", posts=[post(1)])
    second = record(db, "same", posts=[post(2)], status="failed")

    assert first is not None and second is None
    with Session(db) as session:
        rows = session.exec(select(Alert)).all()
        posts = session.exec(select(AlertPost)).all()
        assert [(r.key, r.status, r.deadline_key) for r in rows] == [("same", "sent", REAL.key)]
        assert [p.tweet_x_id for p in posts] == [1]
        assert alert_exists(session, "same") and not alert_exists(session, "other")


def test_done_slots_and_last_alert(db):
    seed(db)
    with Session(db) as session:
        assert done_slots(session, REAL.key) == {}
        assert status_rows(session, REAL.key) == (None, 0)

    record(db, "d", slot=120, at=NOW)
    record(db, "n", kind="news", slot=30, status="skipped", at=NOW + timedelta(hours=1))
    record(db, "b1", kind="breaking", slot=None, status="failed", at=NOW + timedelta(hours=2))
    record(db, "x", status="failed", deadline_value=OTHER, at=NOW + timedelta(hours=3))

    with Session(db) as session:
        slots = done_slots(session, REAL.key)
        assert set(slots) == {120, 30}
        assert slots[30].status == "skipped"
        last, failed = status_rows(session, REAL.key)
        assert (last.key, last.kind, last.status, last.as_of) == (
            "b1",
            "breaking",
            "failed",
            NOW + timedelta(hours=2),
        )
        assert failed == 1
