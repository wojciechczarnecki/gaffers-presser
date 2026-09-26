import logging
from datetime import UTC, datetime

import httpx
from sqlmodel import Session, select

from app.fpl.cli import run_command
from app.fpl.models import Gameweek
from app.fpl.reference import apply_bootstrap
from app.fpl.schemas import Bootstrap
from tests.fpl.fakes import FakeFpl, synthetic_league
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)
LEAGUE_1 = 987654301


def _all_tables_empty(engine) -> bool:
    from sqlmodel import SQLModel

    with Session(engine) as session:
        for table in SQLModel.metadata.tables.values():
            count = session.execute(table.select()).first()
            if count is not None:
                return False
    return True


def test_reference_sync_returns_zero_and_stores_data(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    code = run_command(["reference-sync"], engine=db, client=client, league_ids_raw="", now=NOW)
    assert code == 0
    with Session(db) as session:
        assert len(session.exec(select(Gameweek)).all()) == 38


def test_unavailable_api_writes_nothing(db, capsys):
    fake = FakeFpl(
        {
            "bootstrap-static/": load("bootstrap-static"),
            "fixtures/": lambda request: httpx.Response(503),
        }
    )
    client = fake.client(sleep=lambda _: None)
    code = run_command(["reference-sync"], engine=db, client=client, league_ids_raw="", now=NOW)
    assert code == 1
    assert "fixtures" in capsys.readouterr().err
    assert _all_tables_empty(db)


def test_payload_error_writes_nothing(db, capsys):
    bad_bootstrap = load("bootstrap-static")
    del bad_bootstrap["elements"][0]["status"]
    fake = FakeFpl({"bootstrap-static/": bad_bootstrap, "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    code = run_command(["reference-sync"], engine=db, client=client, league_ids_raw="", now=NOW)
    assert code == 1
    err = capsys.readouterr().err
    assert "bootstrap-static" in err
    assert "elements.0.status" in err
    assert _all_tables_empty(db)


def test_deadline_snapshot_at_deadline_writes_nothing(db, capsys):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static")})
    client = fake.client(sleep=lambda _: None)
    code = run_command(
        ["deadline-snapshot", "--gameweek", "1"],
        engine=db,
        client=client,
        league_ids_raw="",
        now=NOW,
    )
    assert code == 1
    assert "gameweek 1" in capsys.readouterr().err
    assert _all_tables_empty(db)


def _player_ids(n: int = 15) -> list[int]:
    payload = load("bootstrap-static")
    return [e["id"] for e in payload["elements"][:n]]


def test_league_sync_rejects_bad_league_ids(db, capsys):
    fake = FakeFpl({})
    client = fake.client(sleep=lambda _: None)
    code = run_command(
        ["league-sync", "--gameweek", "1"],
        engine=db,
        client=client,
        league_ids_raw="1,abc",
        now=NOW,
    )
    assert code == 1
    assert "FPL_LEAGUE_IDS" in capsys.readouterr().err
    assert fake.requests == []


def test_league_sync_before_deadline_writes_nothing(db, capsys):
    with Session(db) as session:
        apply_bootstrap(session, Bootstrap.model_validate(load("bootstrap-static")), NOW)
        session.commit()

    routes = synthetic_league(LEAGUE_1, [880000001], gameweeks=[6], player_ids=_player_ids())
    fake = FakeFpl(
        {"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures"), **routes}
    )
    client = fake.client(sleep=lambda _: None)

    code = run_command(
        ["league-sync", "--gameweek", "6"],
        engine=db,
        client=client,
        league_ids_raw=str(LEAGUE_1),
        now=NOW,
    )
    assert code == 1
    assert "gameweek 6" in capsys.readouterr().err


def test_failure_on_last_manager_rolls_back(db):
    entry_ids = [880000001, 880000002]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    routes["entry/880000002/transfers/"] = lambda request: httpx.Response(503)
    fake = FakeFpl(
        {"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures"), **routes}
    )
    client = fake.client(sleep=lambda _: None, max_attempts=1)

    code = run_command(
        ["league-sync", "--gameweek", "1"],
        engine=db,
        client=client,
        league_ids_raw=str(LEAGUE_1),
        now=NOW,
    )
    assert code == 1
    assert _all_tables_empty(db)


def test_logs_carry_no_private_data(db, caplog):
    entry_ids = [880000001, 880000002]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=_player_ids())
    fake = FakeFpl(
        {"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures"), **routes}
    )
    client = fake.client(sleep=lambda _: None)

    with caplog.at_level(logging.DEBUG):
        code = run_command(
            ["league-sync", "--gameweek", "1"],
            engine=db,
            client=client,
            league_ids_raw=str(LEAGUE_1),
            now=NOW,
        )
    assert code == 0
    assert "league sync:" in caplog.text
    assert str(LEAGUE_1) not in caplog.text
    for entry_id in entry_ids:
        assert str(entry_id) not in caplog.text
        assert f"Synthetic Manager {entry_id}" not in caplog.text
        assert f"Synthetic XI {entry_id}" not in caplog.text


def test_results_sync_unchecked_gameweek_writes_nothing(db, capsys):
    fake = FakeFpl(
        {
            "bootstrap-static/": load("bootstrap-static"),
            "fixtures/": load("fixtures"),
            "event/30/live/": {"elements": []},
        }
    )
    client = fake.client(sleep=lambda _: None)
    code = run_command(
        ["results-sync", "--gameweek", "30"], engine=db, client=client, league_ids_raw="", now=NOW
    )
    assert code == 1
    assert "gameweek 30" in capsys.readouterr().err
    assert _all_tables_empty(db)


def test_backfill_rejects_bad_league_ids(db, capsys):
    fake = FakeFpl({})
    client = fake.client(sleep=lambda _: None)
    code = run_command(["backfill"], engine=db, client=client, league_ids_raw="", now=NOW)
    assert code == 1
    assert "FPL_LEAGUE_IDS" in capsys.readouterr().err
    assert fake.requests == []


def test_not_found_is_a_clean_error(db, capsys):
    fake = FakeFpl({"bootstrap-static/": httpx.Response(404)})
    client = fake.client(sleep=lambda _: None)
    code = run_command(["reference-sync"], engine=db, client=client, league_ids_raw="", now=NOW)
    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err


def test_database_error_is_a_clean_error_without_private_data(db, capsys):
    entry_ids = [880000001]
    routes = synthetic_league(LEAGUE_1, entry_ids, gameweeks=[1], player_ids=[99999999])
    fake = FakeFpl(
        {"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures"), **routes}
    )
    client = fake.client(sleep=lambda _: None)

    code = run_command(
        ["league-sync", "--gameweek", "1"],
        engine=db,
        client=client,
        league_ids_raw=str(LEAGUE_1),
        now=NOW,
    )

    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("error: database error")
    assert "Traceback" not in err
    for secret in (str(LEAGUE_1), "880000001", "Synthetic", "99999999"):
        assert secret not in err
    assert _all_tables_empty(db)


def test_engine_hides_statement_parameters(db):
    assert db.hide_parameters is True
