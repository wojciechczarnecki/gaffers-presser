import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy import text as sa_text
from sqlmodel import Session, SQLModel
from testcontainers.postgres import PostgresContainer

import app.fpl.models  # noqa: F401
import app.tweets.models  # noqa: F401
import app.worker.models  # noqa: F401
from app.db.engine import make_engine
from app.fpl.reference import apply_bootstrap
from app.fpl.schemas import Bootstrap
from tests.conftest import BACKEND_DIR, run_alembic
from tests.fpl.fakes import table_contents
from tests.fpl.payloads import load

NOW = datetime(2026, 9, 26, tzinfo=UTC)


@pytest.fixture(scope="module")
def migration_url():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        yield container.get_connection_url()


def test_upgrade_downgrade_upgrade(migration_url):
    engine = make_engine(migration_url)

    run_alembic(migration_url, "upgrade", "head")
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    assert tables == set(SQLModel.metadata.tables.keys()) | {"alembic_version"}

    run_alembic(migration_url, "downgrade", "base")
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    assert tables == {"alembic_version"}

    run_alembic(migration_url, "upgrade", "head")
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    assert tables == set(SQLModel.metadata.tables.keys()) | {"alembic_version"}


def test_models_match_migration(migration_url):
    run_alembic(migration_url, "upgrade", "head")
    engine = make_engine(migration_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, SQLModel.metadata)
    assert diff == []


def test_job_run_migration_keeps_collector_data():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        url = container.get_connection_url()
        run_alembic(url, "upgrade", "0001")
        engine = make_engine(url)

        with Session(engine) as session:
            apply_bootstrap(session, Bootstrap.model_validate(load("bootstrap-static")), NOW)
            session.commit()

        collector_tables = set(SQLModel.metadata.tables.keys()) - {"job_run", "tweet", "tweet_poll"}
        with Session(engine) as session:
            before = table_contents(session, tables=collector_tables)

        run_alembic(url, "upgrade", "0002")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "job_run" in tables
        with Session(engine) as session:
            assert table_contents(session, tables=collector_tables) == before

        run_alembic(url, "downgrade", "-1")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "job_run" not in tables
        with Session(engine) as session:
            assert table_contents(session, tables=collector_tables) == before


def test_tweet_migration_adds_only_new_tables():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        url = container.get_connection_url()
        run_alembic(url, "upgrade", "0002")
        engine = make_engine(url)

        with Session(engine) as session:
            apply_bootstrap(session, Bootstrap.model_validate(load("bootstrap-static")), NOW)
            session.commit()
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO job_run (job, season, gameweek_fpl_id, started_at,"
                    " finished_at, outcome) VALUES ('reference_sync', NULL, NULL,"
                    " :now, :now, 'succeeded')"
                ),
                {"now": NOW},
            )

        other_tables = set(SQLModel.metadata.tables.keys()) - {"tweet", "tweet_poll"}
        with Session(engine) as session:
            before = table_contents(session, tables=other_tables)

        run_alembic(url, "upgrade", "head")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert {"tweet", "tweet_poll"} <= tables
        with Session(engine) as session:
            assert table_contents(session, tables=other_tables) == before

        run_alembic(url, "downgrade", "-1")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "tweet" not in tables
        assert "tweet_poll" not in tables
        assert "job_run" in tables
        with Session(engine) as session:
            assert table_contents(session, tables=other_tables) == before


def test_alembic_cli_runs_from_backend(migration_url):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    alembic = str(Path(sys.executable).parent / "alembic")
    result = subprocess.run(
        [alembic, "-x", f"url={migration_url}", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
