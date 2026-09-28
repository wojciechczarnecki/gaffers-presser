import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlmodel import Session
from testcontainers.postgres import PostgresContainer

from app.db.engine import make_engine
from app.worker.cli import WORKER_LOG_HANDLER

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


@pytest.fixture(autouse=True)
def _drop_worker_log_handler():
    # `python -m app.worker` adds a root handler bound to the stream of the moment; under
    # CliRunner that stream is closed after the test, so the handler must not outlive it.
    root = logging.getLogger()
    level = root.level
    yield
    for handler in [h for h in root.handlers if h.get_name() == WORKER_LOG_HANDLER]:
        root.removeHandler(handler)
    root.setLevel(level)


@contextmanager
def held_advisory_lock(engine: Engine, key: int) -> Iterator[Callable[[], None]]:
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        finally:
            # A pooled connection keeps a session-held advisory lock; a real disconnect
            # guarantees nothing leaks into the next test.
            connection.invalidate()
            connection.close()

    try:
        yield release
    finally:
        release()


def wait_for_lock_waiter(engine: Engine, key: int, timeout: float = 10.0) -> None:
    query = text(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key AND NOT granted"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            if connection.execute(query, {"key": key}).scalar():
                return
        time.sleep(0.05)
    raise AssertionError(f"nobody waited for advisory lock {key} within {timeout} s")
