import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

import app.fpl.models  # noqa: F401
from app.db.engine import make_engine
from tests.conftest import run_alembic


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
