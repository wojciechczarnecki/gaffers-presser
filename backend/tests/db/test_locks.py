import threading
from datetime import UTC, datetime

from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.fpl.cli import Deps, app
from app.fpl.models import Gameweek
from tests.fpl.fakes import FakeFpl
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def test_cli_job_waits_for_running_job(db):
    fake = FakeFpl({"bootstrap-static/": load("bootstrap-static"), "fixtures/": load("fixtures")})
    client = fake.client(sleep=lambda _: None)
    deps = Deps(engine=db, client=client, league_ids_raw="", now=NOW)

    lock_session = Session(db)
    lock_session.begin()
    lock_session.execute(text("SELECT pg_advisory_xact_lock(8002002)"))

    result_box: dict = {}

    def run_cli() -> None:
        result_box["result"] = CliRunner().invoke(app, ["reference-sync"], obj=deps)

    thread = threading.Thread(target=run_cli)
    thread.start()
    thread.join(timeout=1)

    assert thread.is_alive()
    with Session(db) as session:
        assert session.exec(select(Gameweek)).all() == []

    lock_session.commit()
    lock_session.close()

    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result_box["result"].exit_code == 0
    with Session(db) as session:
        assert len(session.exec(select(Gameweek)).all()) == 38
