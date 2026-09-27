from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlmodel import Session
from testcontainers.postgres import PostgresContainer

from app.db.engine import make_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_alembic(url: str, *args: str) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    getattr(command, args[0])(config, *args[1:])


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def db_engine(postgres_url):
    run_alembic(postgres_url, "upgrade", "head")
    return make_engine(postgres_url)


@pytest.fixture
def db(db_engine):
    yield db_engine
    with db_engine.connect() as conn:
        tables = (
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables"
                    " WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        if tables:
            names = ", ".join(f'"{name}"' for name in tables)
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            conn.commit()


@pytest.fixture
def db_session(db):
    with Session(db) as session:
        yield session
