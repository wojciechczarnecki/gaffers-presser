from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.fpl.errors import JobError
from app.fpl.leagues import sync_leagues
from app.fpl.models import (
    League,
    LeagueMembership,
    LeagueStanding,
    Manager,
    ManagerAutoSub,
    ManagerChip,
    ManagerGameweek,
    ManagerPick,
    ManagerTransfer,
)
from app.fpl.reference import apply_bootstrap
from app.fpl.schemas import Bootstrap
from tests.fpl.fakes import FakeFpl, synthetic_league, table_contents
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)
LEAGUE_1 = 987654301
LEAGUE_2 = 987654302


def _load_reference(session):
    apply_bootstrap(session, Bootstrap.model_validate(load("bootstrap-static")), NOW)
    return "2026/27"


def _player_ids(n: int = 15) -> list[int]:
    payload = load("bootstrap-static")
    return [e["id"] for e in payload["elements"][:n]]


def test_two_page_league_stores_every_member(db_session):
    _load_reference(db_session)
    entry_ids = list(range(880000001, 880000001 + 51))
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    memberships = db_session.exec(
        select(LeagueMembership).where(LeagueMembership.league_fpl_id == LEAGUE_1)
    ).all()
    assert len(memberships) == 51
    managers = db_session.exec(select(Manager)).all()
    assert len(managers) == 51
    standings = db_session.exec(
        select(LeagueStanding).where(LeagueStanding.league_fpl_id == LEAGUE_1)
    ).all()
    assert len(standings) == 51
    assert {s.rank for s in standings} == set(range(1, 52))


def test_manager_in_two_leagues_stored_once(db_session):
    _load_reference(db_session)
    entry_ids = [880000001, 880000002]
    routes_1 = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    routes_2 = synthetic_league(LEAGUE_2, entry_ids, gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl({**routes_1, **routes_2}).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1, LEAGUE_2], [1], NOW)
    db_session.commit()

    managers = db_session.exec(select(Manager)).all()
    assert len(managers) == 2
    memberships = db_session.exec(select(LeagueMembership)).all()
    assert len(memberships) == 4


def test_before_deadline_fails(db_session):
    _load_reference(db_session)
    entry_ids = [880000001]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[6], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    before_gw6_deadline = datetime(2026, 9, 26, tzinfo=UTC)
    with pytest.raises(JobError, match="gameweek 6 deadline has not passed"):
        sync_leagues(db_session, client, [LEAGUE_1], [6], before_gw6_deadline)


def _picks_route(routes, entry_id, gw):
    return routes[f"entry/{entry_id}/event/{gw}/picks/"]


def test_manager_gameweek_data(db_session):
    _load_reference(db_session)
    entry_ids = [880000001, 880000002]
    player_ids = _player_ids()
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=player_ids)
    payload = _picks_route(routes, 880000001, 1)
    payload["active_chip"] = "3xc"
    payload["automatic_subs"] = [
        {"element_in": player_ids[11], "element_out": player_ids[3], "event": 1},
        {"element_in": player_ids[12], "element_out": player_ids[7], "event": 1},
    ]
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    picks = db_session.exec(
        select(ManagerPick).where(ManagerPick.entry_id == 880000001).order_by(ManagerPick.position)
    ).all()
    assert [
        (p.position, p.player_fpl_id, p.multiplier, p.is_captain, p.is_vice_captain) for p in picks
    ] == [
        (p["position"], p["element"], p["multiplier"], p["is_captain"], p["is_vice_captain"])
        for p in payload["picks"]
    ]

    gw = db_session.get(ManagerGameweek, ("2026/27", 880000001, 1))
    assert gw.has_team is True
    assert gw.active_chip == "3xc"
    history = payload["entry_history"]
    for field in (
        "points",
        "total_points",
        "event_transfers",
        "event_transfers_cost",
        "points_on_bench",
        "bank",
        "value",
        "overall_rank",
    ):
        assert getattr(gw, field) == history[field], field

    subs = db_session.exec(select(ManagerAutoSub).where(ManagerAutoSub.entry_id == 880000001)).all()
    assert sorted((s.player_out_fpl_id, s.player_in_fpl_id) for s in subs) == sorted(
        (s["element_out"], s["element_in"]) for s in payload["automatic_subs"]
    )
    other = db_session.get(ManagerGameweek, ("2026/27", 880000002, 1))
    assert other.active_chip is None


def test_transfers_and_chips(db_session):
    _load_reference(db_session)
    entry_ids = [880000001]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1, 2], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1, 2], NOW)
    db_session.commit()

    transfers = db_session.exec(
        select(ManagerTransfer).where(ManagerTransfer.entry_id == 880000001)
    ).all()
    stored = sorted(
        (
            t.made_at,
            t.gameweek_fpl_id,
            t.player_in_fpl_id,
            t.player_out_fpl_id,
            t.player_in_cost,
            t.player_out_cost,
        )
        for t in transfers
    )
    expected = sorted(
        (
            datetime.fromisoformat(t["time"]),
            t["event"],
            t["element_in"],
            t["element_out"],
            t["element_in_cost"],
            t["element_out_cost"],
        )
        for t in routes["entry/880000001/transfers/"]
    )
    assert stored == expected

    chips = db_session.exec(select(ManagerChip).where(ManagerChip.entry_id == 880000001)).all()
    assert [(c.name, c.gameweek_fpl_id, c.played_at) for c in chips] == [
        (c["name"], c["event"], datetime.fromisoformat(c["time"]))
        for c in routes["entry/880000001/history/"]["chips"]
    ]


def test_standings_rows_match_payload(db_session):
    _load_reference(db_session)
    entry_ids = [880000001, 880000002]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    page = routes[f"leagues-classic/{LEAGUE_1}/standings/?page_standings=1"]
    for r in page["standings"]["results"]:
        standing = db_session.get(LeagueStanding, ("2026/27", LEAGUE_1, 5, r["entry"]))
        assert (standing.rank, standing.event_total, standing.total) == (
            r["rank"],
            r["event_total"],
            r["total"],
        )
        manager = db_session.get(Manager, ("2026/27", r["entry"]))
        assert (manager.team_name, manager.manager_name) == (r["entry_name"], r["player_name"])


def test_manager_without_team_for_gameweek(db_session):
    _load_reference(db_session)
    entry_ids = [880000001, 880000002]
    player_ids = _player_ids()
    seeded = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=player_ids)
    _picks_route(seeded, 880000001, 1)["automatic_subs"] = [
        {"element_in": player_ids[11], "element_out": player_ids[3], "event": 1}
    ]
    sync_leagues(db_session, FakeFpl(seeded).client(sleep=lambda _: None), [LEAGUE_1], [1], NOW)
    db_session.commit()

    routes = synthetic_league(
        LEAGUE_1,
        entry_ids,
        gameweeks=[1],
        player_ids=player_ids,
        no_team_for={880000001: {1}},
    )
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    missing = db_session.get(ManagerGameweek, ("2026/27", 880000001, 1))
    assert missing.has_team is False
    assert missing.points is None
    assert missing.active_chip is None
    for model in (ManagerPick, ManagerAutoSub):
        rows = db_session.exec(select(model).where(model.entry_id == 880000001)).all()
        assert rows == []

    complete = db_session.get(ManagerGameweek, ("2026/27", 880000002, 1))
    assert complete.has_team is True
    picks = db_session.exec(select(ManagerPick).where(ManagerPick.entry_id == 880000002)).all()
    assert len(picks) == 15


def test_at_exact_deadline_is_accepted(db_session):
    payload = load("bootstrap-static")
    apply_bootstrap(db_session, Bootstrap.model_validate(payload), NOW)
    gw1_deadline = datetime.fromisoformat(payload["events"][0]["deadline_time"])
    routes = synthetic_league(LEAGUE_1, [880000001], gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], gw1_deadline)
    db_session.commit()

    assert db_session.get(ManagerGameweek, ("2026/27", 880000001, 1)).has_team is True
    assert db_session.get(LeagueStanding, ("2026/27", LEAGUE_1, 1, 880000001)) is not None


def test_nonexistent_gameweek_fails(db_session):
    _load_reference(db_session)
    routes = synthetic_league(LEAGUE_1, [880000001], gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    with pytest.raises(JobError):
        sync_leagues(db_session, client, [LEAGUE_1], [99], NOW)


def test_empty_league_and_manager_without_transfers_or_chips(db_session):
    _load_reference(db_session)
    empty = synthetic_league(LEAGUE_2, [], gameweeks=[1], player_ids=_player_ids())
    routes = synthetic_league(LEAGUE_1, [880000001], gameweeks=[1], player_ids=_player_ids())
    routes["entry/880000001/transfers/"] = []
    routes["entry/880000001/history/"] = {"chips": []}
    client = FakeFpl({**empty, **routes}).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1, LEAGUE_2], [1], NOW)
    db_session.commit()

    assert db_session.get(League, ("2026/27", LEAGUE_2)) is not None
    memberships = db_session.exec(
        select(LeagueMembership).where(LeagueMembership.league_fpl_id == LEAGUE_2)
    ).all()
    assert memberships == []
    assert db_session.exec(select(ManagerTransfer)).all() == []
    assert db_session.exec(select(ManagerChip)).all() == []
    assert db_session.get(ManagerGameweek, ("2026/27", 880000001, 1)).has_team is True


def test_rerun_is_idempotent(db_session):
    _load_reference(db_session)
    entry_ids = [880000001, 880000002]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()
    before = table_contents(db_session)

    client = FakeFpl(routes).client(sleep=lambda _: None)
    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()
    after = table_contents(db_session)

    assert before == after


def test_entry_repeated_across_pages_is_stored_once(db_session):
    _load_reference(db_session)
    entry_ids = list(range(880000001, 880000001 + 51))
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    page_1 = routes[f"leagues-classic/{LEAGUE_1}/standings/?page_standings=1"]
    page_2 = routes[f"leagues-classic/{LEAGUE_1}/standings/?page_standings=2"]
    shifted = dict(page_1["standings"]["results"][-1], rank=51, total=1)
    page_2["standings"]["results"].insert(0, shifted)
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    standings = db_session.exec(select(LeagueStanding)).all()
    assert len(standings) == 51
    moved = db_session.get(LeagueStanding, ("2026/27", LEAGUE_1, 5, shifted["entry"]))
    assert (moved.rank, moved.total) == (51, 1)
