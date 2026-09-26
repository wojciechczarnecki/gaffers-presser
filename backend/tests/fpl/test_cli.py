from datetime import UTC, datetime

import httpx
from sqlmodel import Session, select

from app.fpl.cli import run_command
from app.fpl.models import Gameweek
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)


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


def test_not_found_is_a_clean_error(db, capsys):
    fake = FakeFpl({"bootstrap-static/": httpx.Response(404)})
    client = fake.client(sleep=lambda _: None)
    code = run_command(["reference-sync"], engine=db, client=client, league_ids_raw="", now=NOW)
    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err
