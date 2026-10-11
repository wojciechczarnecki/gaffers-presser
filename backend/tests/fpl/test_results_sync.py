import copy
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.fpl.errors import JobError
from app.fpl.models import Fixture, PlayerFixtureStat, PlayerGameweekResult, RawPayload
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

        assert row.explain == el["explain"]
        assert row.live_stats == el["stats"]

    archive = {a.endpoint: a for a in db_session.exec(select(RawPayload)).all()}
    assert set(archive) == {"event/1/live", "fixtures"}
    assert archive["event/1/live"].payload == live_payload
    assert archive["fixtures"].payload == load("fixtures")
    assert {a.gameweek_fpl_id for a in archive.values()} == {1}

    stored = {f.fpl_id: f for f in db_session.exec(select(Fixture)).all()}
    gw1 = [f for f in load("fixtures") if f["event"] == 1]
    assert gw1
    for f in gw1:
        row = stored[f["id"]]
        assert (row.team_h_fpl_id, row.team_a_fpl_id, row.team_h_score, row.team_a_score) == (
            f["team_h"],
            f["team_a"],
            f["team_h_score"],
            f["team_a_score"],
        )


def _stored_fixture_stats(session) -> set[tuple]:
    return {
        (r.fixture_fpl_id, r.identifier, r.player_fpl_id, r.value, r.side)
        for r in session.exec(select(PlayerFixtureStat)).all()
    }


def _payload_fixture_stats(fixtures: list[dict], gameweek: int) -> set[tuple]:
    return {
        (f["id"], stat["identifier"], entry["element"], entry["value"], side)
        for f in fixtures
        if f["event"] == gameweek
        for stat in f["stats"]
        for side in ("h", "a")
        for entry in stat[side]
    }


def test_fixture_stats_stored_per_player_and_fixture(db_session):
    _load_reference(db_session)
    fixtures_payload = load("fixtures")
    sync_results(db_session, _client(fixtures=fixtures_payload), 1, NOW)
    db_session.commit()

    expected = _payload_fixture_stats(fixtures_payload, 1)
    assert expected
    assert {identifier for _, identifier, *_ in expected} >= {"bps", "bonus", "goals_scored"}
    assert _stored_fixture_stats(db_session) == expected


def test_fixture_stat_of_an_unknown_player_skipped(db_session):
    _load_reference(db_session)
    fixtures_payload = load("fixtures")
    known = {e["id"] for e in load("bootstrap-static")["elements"]}
    unknown = max(known) + 1000
    target = next(
        stat for f in fixtures_payload if f["event"] == 1 for stat in f["stats"] if stat["h"]
    )
    target["h"].append({"element": unknown, "value": 1})

    sync_results(db_session, _client(fixtures=fixtures_payload), 1, NOW)
    db_session.commit()

    stored = _stored_fixture_stats(db_session)
    assert stored
    assert not [row for row in stored if row[2] == unknown]
    expected = {row for row in _payload_fixture_stats(fixtures_payload, 1) if row[2] != unknown}
    assert stored == expected


def test_resync_replaces_fixture_stats(db_session):
    _load_reference(db_session)
    sync_results(db_session, _client(), 1, NOW)
    db_session.commit()

    trimmed = load("fixtures")
    removed = next(
        (f, stat)
        for f in trimmed
        if f["event"] == 1
        for stat in f["stats"]
        if stat["identifier"] == "yellow_cards" and (stat["h"] or stat["a"])
    )
    removed[1]["h"] = []
    removed[1]["a"] = []
    sync_results(db_session, _client(fixtures=trimmed), 1, NOW)
    db_session.commit()

    assert _stored_fixture_stats(db_session) == _payload_fixture_stats(trimmed, 1)
    assert not [
        t for t in _stored_fixture_stats(db_session) if t[:2] == (removed[0]["id"], "yellow_cards")
    ]


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
    assert row.explain == target["explain"]


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


def test_nonexistent_gameweek_fails(db_session):
    _load_reference(db_session)
    with pytest.raises(JobError, match="gameweek 99 does not exist"):
        sync_results(db_session, _client(), 99, NOW)


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

    assert len(after.pop("raw_payload")) == len(before.pop("raw_payload")) + 2
    assert after == before
