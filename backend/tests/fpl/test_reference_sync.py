import copy
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.fpl.models import Fixture, Gameweek, Player, PlayerFlagChange, Team
from app.fpl.reference import apply_bootstrap, sync_reference
from app.fpl.schemas import Bootstrap
from tests.fpl.fakes import FakeFpl, table_contents
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _client(bootstrap=None, fixtures=None):
    fake = FakeFpl(
        {
            "bootstrap-static/": bootstrap if bootstrap is not None else load("bootstrap-static"),
            "fixtures/": fixtures if fixtures is not None else load("fixtures"),
        }
    )
    return fake.client(sleep=lambda _: None)


def test_counts_match_payload(db_session):
    bootstrap_payload = load("bootstrap-static")
    fixtures_payload = load("fixtures")
    client = _client(bootstrap_payload, fixtures_payload)
    sync_reference(db_session, client, NOW)
    db_session.commit()

    assert len(db_session.exec(select(Gameweek)).all()) == len(bootstrap_payload["events"])
    assert len(db_session.exec(select(Team)).all()) == len(bootstrap_payload["teams"])
    players = db_session.exec(select(Player)).all()
    assert len(players) == len(bootstrap_payload["elements"])
    fixtures_rows = db_session.exec(select(Fixture)).all()
    assert len(fixtures_rows) == len(fixtures_payload)
    scored = [f for f in fixtures_rows if f.team_h_score is not None]
    payload_scored = [f for f in fixtures_payload if f["team_h_score"] is not None]
    assert len(scored) == len(payload_scored)


def test_season_label(db_session):
    client = _client()
    season = sync_reference(db_session, client, NOW)
    assert season == "2026/27"


def test_new_season_keeps_previous_rows(db_session):
    bootstrap_payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()

    shifted = copy.deepcopy(bootstrap_payload)
    for event in shifted["events"]:
        deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
        event["deadline_time"] = (deadline - timedelta(days=365)).isoformat()
    other_season = apply_bootstrap(db_session, Bootstrap.model_validate(shifted), NOW)
    db_session.commit()

    assert other_season == "2025/26"
    seasons = {row.season for row in db_session.exec(select(Team)).all()}
    assert {"2026/27", "2025/26"} <= seasons
    current_teams = db_session.exec(select(Team).where(Team.season == "2026/27")).all()
    assert len(current_teams) == len(bootstrap_payload["teams"])


def test_player_added_moved_and_removed(db_session):
    bootstrap_payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()

    changed = copy.deepcopy(bootstrap_payload)
    removed_element = changed["elements"].pop()
    moved_element = changed["elements"][0]
    other_team = next(t["id"] for t in changed["teams"] if t["id"] != moved_element["team"])
    moved_element["team"] = other_team
    new_id = max(e["id"] for e in bootstrap_payload["elements"]) + 1000
    new_element = copy.deepcopy(bootstrap_payload["elements"][1])
    new_element["id"] = new_id
    changed["elements"].append(new_element)

    apply_bootstrap(db_session, Bootstrap.model_validate(changed), NOW)
    db_session.commit()

    season = "2026/27"
    added = db_session.get(Player, (season, new_id))
    assert added is not None

    moved = db_session.get(Player, (season, moved_element["id"]))
    assert moved.team_fpl_id == other_team

    still_there = db_session.get(Player, (season, removed_element["id"]))
    assert still_there is not None


def test_stored_datetimes_are_utc(db_session):
    client = _client()
    sync_reference(db_session, client, NOW)
    db_session.commit()
    gw = db_session.exec(select(Gameweek)).first()
    assert gw.deadline_at.utcoffset() == timedelta(0)


def test_flag_baseline(db_session):
    bootstrap_payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()
    rows = db_session.exec(select(PlayerFlagChange)).all()
    assert len(rows) == len(bootstrap_payload["elements"])


def test_flag_change_rows(db_session):
    bootstrap_payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()

    changed = copy.deepcopy(bootstrap_payload)
    changed["elements"][0]["status"] = "i"
    changed["elements"][0]["news"] = "Injured, expected back in October"
    changed["elements"][1]["chance_of_playing_this_round"] = 50

    later = NOW + timedelta(hours=1)
    apply_bootstrap(db_session, Bootstrap.model_validate(changed), later)
    db_session.commit()

    rows = db_session.exec(
        select(PlayerFlagChange).where(PlayerFlagChange.observed_at == later)
    ).all()
    assert len(rows) == 2


def test_unchanged_payload_writes_no_flag_rows(db_session):
    bootstrap_payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()

    later = NOW + timedelta(hours=1)
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), later)
    db_session.commit()

    rows = db_session.exec(
        select(PlayerFlagChange).where(PlayerFlagChange.observed_at == later)
    ).all()
    assert rows == []


def test_news_added_only_change_writes_no_flag_rows(db_session):
    bootstrap_payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(bootstrap_payload), NOW)
    db_session.commit()

    changed = copy.deepcopy(bootstrap_payload)
    changed["elements"][0]["news_added"] = "2026-09-02T10:00:00Z"

    later = NOW + timedelta(hours=1)
    apply_bootstrap(db_session, Bootstrap.model_validate(changed), later)
    db_session.commit()

    rows = db_session.exec(
        select(PlayerFlagChange).where(PlayerFlagChange.observed_at == later)
    ).all()
    assert rows == []


def test_rerun_is_idempotent(db_session):
    client = _client()
    sync_reference(db_session, client, NOW)
    db_session.commit()
    before = table_contents(db_session)

    client = _client()
    sync_reference(db_session, client, NOW)
    db_session.commit()
    after = table_contents(db_session)

    assert before == after
