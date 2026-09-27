from datetime import UTC, datetime

from sqlmodel import select

from app.fpl.backfill import backfill
from app.fpl.models import (
    DeadlineSnapshotPlayer,
    ManagerGameweek,
    ManagerPick,
    PlayerGameweekResult,
    RawPayload,
)
from tests.fpl.fakes import FakeFpl, synthetic_league, table_contents
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 8, tzinfo=UTC)
LEAGUE_1 = 987654301


def _backfill_bootstrap() -> dict:
    payload = load("bootstrap-static")
    for event in payload["events"]:
        if event["id"] >= 4:
            event["finished"] = False
            event["data_checked"] = False
    return payload


def _player_ids(n: int = 15) -> list[int]:
    payload = load("bootstrap-static")
    return [e["id"] for e in payload["elements"][:n]]


def _routes() -> dict:
    bootstrap_payload = _backfill_bootstrap()
    entry_ids = [880000001, 880000002, 880000003]
    league_routes = synthetic_league(
        LEAGUE_1, entry_ids, gameweeks=[1, 2, 3], player_ids=_player_ids()
    )
    return {
        "bootstrap-static/": bootstrap_payload,
        "fixtures/": load("fixtures"),
        "event/1/live/": load("event-1-live"),
        "event/2/live/": load("event-2-live"),
        "event/3/live/": load("event-3-live"),
        **league_routes,
    }


def test_backfill_gw1_to_3(db_session):
    client = FakeFpl(_routes()).client(sleep=lambda _: None)
    backfill(db_session, client, [LEAGUE_1], NOW)
    db_session.commit()

    gws = {row.gameweek_fpl_id for row in db_session.exec(select(ManagerGameweek)).all()}
    assert gws == {1, 2, 3}

    pick_gws = {row.gameweek_fpl_id for row in db_session.exec(select(ManagerPick)).all()}
    assert pick_gws == {1, 2, 3}

    result_gws = {
        row.gameweek_fpl_id for row in db_session.exec(select(PlayerGameweekResult)).all()
    }
    assert result_gws == {1, 2, 3}

    assert db_session.exec(select(DeadlineSnapshotPlayer)).all() == []

    archive = db_session.exec(select(RawPayload)).all()
    assert len(archive) == 6


def test_rerun_is_idempotent(db_session):
    routes = _routes()
    client = FakeFpl(routes).client(sleep=lambda _: None)
    backfill(db_session, client, [LEAGUE_1], NOW)
    db_session.commit()
    before = table_contents(db_session)

    client = FakeFpl(routes).client(sleep=lambda _: None)
    backfill(db_session, client, [LEAGUE_1], NOW)
    db_session.commit()
    after = table_contents(db_session)

    assert len(after.pop("raw_payload")) == len(before.pop("raw_payload")) + 6
    assert after == before
