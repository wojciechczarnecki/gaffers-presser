from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlmodel import select

from app.fpl.errors import JobError
from app.fpl.models import DeadlineSnapshotPlayer, RawPayload
from app.fpl.snapshot import take_deadline_snapshot
from tests.fpl.fakes import FakeFpl, table_contents
from tests.fpl.payloads import load

BEFORE_GW6_DEADLINE = datetime(2026, 9, 26, tzinfo=UTC)


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

    rows = {r.player_fpl_id: r for r in db_session.exec(select(DeadlineSnapshotPlayer)).all()}
    assert len(rows) == len(bootstrap_payload["elements"])
    for el in bootstrap_payload["elements"]:
        row = rows[el["id"]]
        assert (
            row.status,
            row.news,
            row.chance_of_playing_this_round,
            row.chance_of_playing_next_round,
            row.selected_by_percent,
            row.now_cost,
            row.captured_at,
        ) == (
            el["status"],
            el["news"],
            el["chance_of_playing_this_round"],
            el["chance_of_playing_next_round"],
            Decimal(el["selected_by_percent"]),
            el["now_cost"],
            BEFORE_GW6_DEADLINE,
        )
    archive = db_session.exec(select(RawPayload)).all()
    assert len(archive) == 1
    assert archive[0].endpoint == "bootstrap-static"
    assert archive[0].gameweek_fpl_id == 6
    assert archive[0].payload == bootstrap_payload


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


def _gw6_deadline() -> datetime:
    return datetime.fromisoformat(load("bootstrap-static")["events"][5]["deadline_time"])


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=1)])
def test_at_or_after_deadline_fails(db_session, offset):
    client = _client()
    with pytest.raises(JobError, match="gameweek 6 deadline has passed"):
        take_deadline_snapshot(db_session, client, 6, _gw6_deadline() + offset)


def test_just_before_deadline_succeeds(db_session):
    client = _client()
    take_deadline_snapshot(db_session, client, 6, _gw6_deadline() - timedelta(seconds=1))
    db_session.commit()
    assert db_session.exec(select(DeadlineSnapshotPlayer)).first() is not None


def test_nonexistent_gameweek_fails(db_session):
    client = _client()
    with pytest.raises(JobError, match="gameweek 99 does not exist"):
        take_deadline_snapshot(db_session, client, 99, BEFORE_GW6_DEADLINE)


def test_rerun_is_idempotent(db_session):
    client = _client()
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()
    before = table_contents(db_session)

    client = _client()
    take_deadline_snapshot(db_session, client, 6, BEFORE_GW6_DEADLINE)
    db_session.commit()
    after = table_contents(db_session)

    assert len(after.pop("raw_payload")) == len(before.pop("raw_payload")) + 1
    assert after == before
