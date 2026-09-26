import copy
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.fpl.errors import JobError
from app.fpl.models import Fixture, PlayerGameweekResult, RawPayload
from app.fpl.reference import apply_bootstrap
from app.fpl.results import sync_results
from app.fpl.schemas import Bootstrap
from tests.fpl.fakes import FakeFpl, table_contents
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def _client(live=None, fixtures=None):
    fake = FakeFpl(
        {
            "event/1/live/": live if live is not None else load("event-1-live"),
            "fixtures/": fixtures if fixtures is not None else load("fixtures"),
        }
    )
    return fake.client(sleep=lambda _: None)


def _load_reference(session):
    apply_bootstrap(session, Bootstrap.model_validate(load("bootstrap-static")), NOW)


def test_results_stored_and_archived(db_session):
    _load_reference(db_session)
    live_payload = load("event-1-live")
    client = _client(live_payload)

    sync_results(db_session, client, 1, NOW)
    db_session.commit()

    rows = db_session.exec(select(PlayerGameweekResult)).all()
    assert len(rows) == len(live_payload["elements"])
    by_id = {r.player_fpl_id: r for r in rows}
    for el in live_payload["elements"]:
        row = by_id[el["id"]]
        assert row.starts == el["stats"]["starts"]
        assert row.minutes == el["stats"]["minutes"]
        assert row.total_points == el["stats"]["total_points"]

    archive = db_session.exec(select(RawPayload)).all()
    assert len(archive) == 2
    assert {a.endpoint for a in archive} == {"event/1/live", "fixtures"}

    scored_fixtures = db_session.exec(select(Fixture).where(Fixture.team_h_score.isnot(None))).all()
    assert len(scored_fixtures) > 0


def test_double_gameweek(db_session):
    _load_reference(db_session)
    live_payload = load("event-1-live")
    fixtures_payload = load("fixtures")

    target = live_payload["elements"][0]
    target["stats"]["minutes"] = 180
    target["stats"]["starts"] = 2
    target["stats"]["total_points"] = 12
    target["explain"] = [
        {
            "fixture": 1,
            "stats": [
                {"identifier": "minutes", "points": 2, "value": 90, "points_modification": 0}
            ],
        },
        {
            "fixture": 9001,
            "stats": [
                {"identifier": "minutes", "points": 2, "value": 90, "points_modification": 0}
            ],
        },
    ]

    bootstrap_payload = load("bootstrap-static")
    player_team = next(e["team"] for e in bootstrap_payload["elements"] if e["id"] == target["id"])
    extra_fixture = copy.deepcopy(fixtures_payload[0])
    extra_fixture["id"] = 9001
    extra_fixture["team_h"] = player_team
    extra_fixture["team_a"] = next(
        t["id"] for t in bootstrap_payload["teams"] if t["id"] != player_team
    )
    fixtures_payload = [*fixtures_payload, extra_fixture]

    client = _client(live_payload, fixtures_payload)
    sync_results(db_session, client, 1, NOW)
    db_session.commit()

    row = db_session.get(PlayerGameweekResult, ("2026/27", target["id"], 1))
    assert row.minutes == 180
    assert row.starts == 2
    assert row.total_points == 12
    assert len(row.explain) == 2


@pytest.mark.parametrize(
    "finished,data_checked",
    [(False, False), (True, False)],
)
def test_unchecked_gameweek_fails(db_session, finished, data_checked):
    bootstrap_payload = load("bootstrap-static")
    bootstrap_payload["events"][0]["finished"] = finished
    bootstrap_payload["events"][0]["data_checked"] = data_checked
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()

    client = _client()
    with pytest.raises(JobError):
        sync_results(db_session, client, 1, NOW)


def test_rerun_is_idempotent(db_session):
    _load_reference(db_session)
    client = _client()
    sync_results(db_session, client, 1, NOW)
    db_session.commit()
    before = table_contents(db_session)

    client = _client()
    sync_results(db_session, client, 1, NOW)
    db_session.commit()
    after = table_contents(db_session)

    before_counts = {name: len(rows) for name, rows in before.items()}
    after_counts = {name: len(rows) for name, rows in after.items()}
    for name in before_counts:
        if name == "raw_payload":
            assert after_counts[name] == before_counts[name] + 2
        else:
            assert after_counts[name] == before_counts[name]
