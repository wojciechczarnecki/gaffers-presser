from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.fpl.errors import JobError
from app.fpl.models import DeadlineSnapshotPlayer, RawPayload
from app.fpl.snapshot import take_deadline_snapshot
from tests.fpl.fakes import FakeFpl, table_contents
from tests.fpl.payloads import load

BEFORE_GW6_DEADLINE = datetime(2026, 9, 26, tzinfo=UTC)
AFTER_GW1_DEADLINE = datetime(2026, 9, 26, tzinfo=UTC)


def _client(bootstrap=None):
    fake = FakeFpl(
        {"bootstrap-static/": bootstrap if bootstrap is not None else load("bootstrap-static")}
    )
    return fake.client(sleep=lambda _: None)


def test_snapshot_stores_every_player_and_archives(db_session):
    bootstrap_payload = load("bootstrap-static")
    client = _client(bootstrap_payload)
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()

    rows = db_session.exec(select(DeadlineSnapshotPlayer)).all()
    assert len(rows) == len(bootstrap_payload["elements"])
    archive = db_session.exec(select(RawPayload)).all()
    assert len(archive) == 1
    assert archive[0].endpoint == "bootstrap-static"
    assert archive[0].gameweek_fpl_id == 6


def test_rerun_replaces_snapshot(db_session):
    bootstrap_payload = load("bootstrap-static")
    client = _client(bootstrap_payload)
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()
    before = db_session.exec(select(DeadlineSnapshotPlayer)).all()

    changed = bootstrap_payload
    changed["elements"][0]["selected_by_percent"] = "99.9"
    client = _client(changed)
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()
    after = db_session.exec(select(DeadlineSnapshotPlayer)).all()

    assert len(after) == len(before)
    changed_row = db_session.get(
        DeadlineSnapshotPlayer, ("2026/27", 6, changed["elements"][0]["id"])
    )
    assert str(changed_row.selected_by_percent) == "99.9"


def test_at_or_after_deadline_fails_and_writes_nothing(db_session):
    client = _client()
    with pytest.raises(JobError):
        take_deadline_snapshot(db_session, client, 1, AFTER_GW1_DEADLINE)


def test_rerun_is_idempotent(db_session):
    client = _client()
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()
    before = table_contents(db_session)

    client = _client()
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()
    after = table_contents(db_session)

    before_counts = {name: len(rows) for name, rows in before.items()}
    after_counts = {name: len(rows) for name, rows in after.items()}
    for name in before_counts:
        if name == "raw_payload":
            assert after_counts[name] == before_counts[name] + 1
        else:
            assert after_counts[name] == before_counts[name]
