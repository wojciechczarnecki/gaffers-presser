from datetime import UTC, datetime

from sqlmodel import select

from app.fpl.leagues import sync_leagues
from app.fpl.models import (
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
    from app.fpl.errors import JobError

    _load_reference(db_session)
    entry_ids = [880000001]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[6], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    before_gw6_deadline = datetime(2026, 9, 26, tzinfo=UTC)
    try:
        sync_leagues(db_session, client, [LEAGUE_1], [6], before_gw6_deadline)
        raised = False
    except JobError:
        raised = True
    assert raised


def test_manager_gameweek_data(db_session):
    _load_reference(db_session)
    entry_ids = [880000001]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    picks = db_session.exec(select(ManagerPick).where(ManagerPick.entry_id == 880000001)).all()
    assert len(picks) == 15
    captain = [p for p in picks if p.is_captain]
    assert len(captain) == 1
    vice = [p for p in picks if p.is_vice_captain]
    assert len(vice) == 1

    gw = db_session.get(ManagerGameweek, ("2026/27", 880000001, 1))
    assert gw.has_team is True
    assert gw.points == 60
    assert gw.total_points == 60


def test_transfers_and_chips(db_session):
    _load_reference(db_session)
    entry_ids = [880000001]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    transfers = db_session.exec(
        select(ManagerTransfer).where(ManagerTransfer.entry_id == 880000001)
    ).all()
    assert len(transfers) == 1
    chips = db_session.exec(select(ManagerChip).where(ManagerChip.entry_id == 880000001)).all()
    assert len(chips) == 1


def test_manager_without_team_for_gameweek(db_session):
    _load_reference(db_session)
    entry_ids = [880000001, 880000002]
    routes = synthetic_league(
        LEAGUE_1,
        entry_ids,
        gameweeks=[1],
        player_ids=_player_ids(),
        no_team_for={880000002: {1}},
    )
    client = FakeFpl(routes).client(sleep=lambda _: None)

    sync_leagues(db_session, client, [LEAGUE_1], [1], NOW)
    db_session.commit()

    complete = db_session.get(ManagerGameweek, ("2026/27", 880000001, 1))
    assert complete.has_team is True
    missing = db_session.get(ManagerGameweek, ("2026/27", 880000002, 1))
    assert missing.has_team is False
    assert missing.points is None
    subs = db_session.exec(select(ManagerAutoSub).where(ManagerAutoSub.entry_id == 880000002)).all()
    assert subs == []


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
