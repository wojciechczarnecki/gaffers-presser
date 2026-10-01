import json
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

import app.delivery.models  # noqa: F401
import app.extraction.models  # noqa: F401
import app.fpl.models  # noqa: F401
import app.retrieval.models  # noqa: F401
import app.tweets.models  # noqa: F401
import app.worker.models  # noqa: F401
from app.db.engine import make_engine
from app.fpl.reference import apply_bootstrap
from app.fpl.schemas import Bootstrap
from app.retrieval.evaluation.dataset import CorpusPost
from app.retrieval.evaluation.schema import EVAL_SCHEMA, load_eval_corpus
from tests.conftest import BACKEND_DIR, run_alembic
from tests.fpl.fakes import DEFAULT_EXCLUDE, table_contents
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

        collector_tables = set(SQLModel.metadata.tables.keys()) - {
            "job_run",
            "tweet",
            "tweet_poll",
            "extraction",
            "extraction_event",
            "post_embedding",
            "delivery_log",
        }
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

        other_tables = set(SQLModel.metadata.tables.keys()) - {
            "tweet",
            "tweet_poll",
            "extraction",
            "extraction_event",
            "post_embedding",
            "delivery_log",
        }
        with Session(engine) as session:
            before = table_contents(session, tables=other_tables)

        run_alembic(url, "upgrade", "0003")
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


def test_extraction_migration_adds_only_new_tables():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        url = container.get_connection_url()
        run_alembic(url, "upgrade", "0003")
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
            conn.execute(
                sa_text(
                    "INSERT INTO tweet (x_id, author_handle, text, created_at,"
                    " first_fetched_at, source, is_repost, is_reply, raw) VALUES"
                    " (1, 'reporter', 'some text', :now, :now, 'list', false, false, '{}')"
                ),
                {"now": NOW},
            )

        other_tables = set(SQLModel.metadata.tables.keys()) - {
            "extraction",
            "extraction_event",
            "post_embedding",
            "delivery_log",
        }
        excluded = DEFAULT_EXCLUDE | {"search_vector", "reposted_author_handle"}
        with Session(engine) as session:
            before = table_contents(session, exclude=excluded, tables=other_tables)

        run_alembic(url, "upgrade", "0004")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert {"extraction", "extraction_event"} <= tables
        with Session(engine) as session:
            assert table_contents(session, exclude=excluded, tables=other_tables) == before

        run_alembic(url, "downgrade", "-1")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "extraction" not in tables
        assert "extraction_event" not in tables
        assert "tweet" in tables
        with Session(engine) as session:
            assert table_contents(session, exclude=excluded, tables=other_tables) == before


EVAL_POST = CorpusPost(
    x_id=2,
    author_handle="reporter",
    text="Saka injury",
    created_at=NOW,
    is_repost=False,
    is_reply=False,
)


def test_retrieval_migration_keeps_data_and_downgrades():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        url = container.get_connection_url()
        run_alembic(url, "upgrade", "0004")
        engine = make_engine(url)

        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO job_run (job, season, gameweek_fpl_id, started_at,"
                    " finished_at, outcome) VALUES ('reference_sync', NULL, NULL,"
                    " :now, :now, 'succeeded')"
                ),
                {"now": NOW},
            )
            conn.execute(
                sa_text(
                    "INSERT INTO tweet (x_id, author_handle, text, created_at,"
                    " first_fetched_at, source, is_repost, is_reply, raw) VALUES"
                    " (1, 'reporter', 'Ødegaard injury', :now, :now, 'list', false, false, '{}')"
                ),
                {"now": NOW},
            )

        other_tables = set(SQLModel.metadata.tables.keys()) - {"post_embedding", "delivery_log"}
        excluded = DEFAULT_EXCLUDE | {"search_vector", "reposted_author_handle"}
        with Session(engine) as session:
            before = table_contents(session, exclude=excluded, tables=other_tables)

        run_alembic(url, "upgrade", "0005")
        with engine.connect() as conn:
            assert "post_embedding" in set(inspect(conn).get_table_names())
            vector = conn.execute(sa_text("SELECT search_vector::text FROM tweet")).scalar_one()
        assert "'odegaard'" in vector
        with Session(engine) as session:
            assert table_contents(session, exclude=excluded, tables=other_tables) == before

        load_eval_corpus(engine, [EVAL_POST])  # what prelabel and evaluate leave behind

        run_alembic(url, "downgrade", "-1")
        with engine.connect() as conn:
            schemas = set(inspect(conn).get_schema_names())
            tables = set(inspect(conn).get_table_names())
            columns = {column["name"] for column in inspect(conn).get_columns("tweet")}
            configs = conn.execute(
                sa_text("SELECT cfgname FROM pg_ts_config WHERE cfgname = 'english_unaccent'")
            ).all()
            extensions = set(
                conn.execute(sa_text("SELECT extname FROM pg_extension")).scalars().all()
            )
        assert EVAL_SCHEMA not in schemas
        assert "post_embedding" not in tables
        assert "search_vector" not in columns
        assert configs == []
        assert not {"vector", "unaccent"} & extensions
        with Session(engine) as session:
            assert table_contents(session, exclude=excluded, tables=other_tables) == before


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


def test_repost_author_migration_backfills_and_downgrades():
    def insert(conn, x_id: int, is_repost: bool, raw: dict) -> None:
        conn.execute(
            sa_text(
                "INSERT INTO tweet (x_id, author_handle, text, created_at, first_fetched_at,"
                " source, is_repost, is_reply, raw) VALUES (:x_id, 'lister', 'text', :now, :now,"
                " 'list', :is_repost, false, CAST(:raw AS jsonb))"
            ),
            {"x_id": x_id, "now": NOW, "is_repost": is_repost, "raw": json.dumps(raw)},
        )

    def originals(conn) -> dict[int, str | None]:
        rows = conn.execute(sa_text("SELECT x_id, reposted_author_handle FROM tweet ORDER BY x_id"))
        return {row[0]: row[1] for row in rows}

    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        url = container.get_connection_url()
        run_alembic(url, "upgrade", "0005")
        engine = make_engine(url)
        with engine.begin() as conn:
            insert(conn, 1, True, {"retweetedTweet": {"user": {"username": "TwscrapeOrigin"}}})
            insert(conn, 2, True, {"retweeted_tweet": {"author": {"userName": "ApiIoOrigin"}}})
            insert(conn, 3, True, {"retweeted_author": {"username": "XApiOrigin"}})
            insert(conn, 4, False, {"text": "an original post"})
            insert(conn, 5, True, {})
            insert(conn, 6, False, {"retweetedTweet": {"user": {"username": "NotARepost"}}})

        other_tables = set(SQLModel.metadata.tables.keys()) - {"tweet", "delivery_log"}
        with Session(engine) as session:
            before = table_contents(session, exclude=DEFAULT_EXCLUDE, tables=other_tables)

        run_alembic(url, "upgrade", "0006")
        with engine.connect() as conn:
            assert originals(conn) == {
                1: "TwscrapeOrigin",
                2: "ApiIoOrigin",
                3: "XApiOrigin",
                4: None,
                5: None,
                6: None,
            }
        with Session(engine) as session:
            assert table_contents(session, exclude=DEFAULT_EXCLUDE, tables=other_tables) == before

        run_alembic(url, "downgrade", "-1")
        with engine.connect() as conn:
            columns = {column["name"] for column in inspect(conn).get_columns("tweet")}
            assert conn.execute(sa_text("SELECT count(*) FROM tweet")).scalar_one() == 6
        assert "reposted_author_handle" not in columns
        with Session(engine) as session:
            assert table_contents(session, exclude=DEFAULT_EXCLUDE, tables=other_tables) == before

        run_alembic(url, "upgrade", "0006")
        with engine.connect() as conn:
            assert originals(conn)[1] == "TwscrapeOrigin"


def test_delivery_migration_adds_only_new_table():
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as container:
        url = container.get_connection_url()
        run_alembic(url, "upgrade", "0006")
        engine = make_engine(url)

        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO job_run (job, season, gameweek_fpl_id, started_at,"
                    " finished_at, outcome) VALUES ('reference_sync', NULL, NULL,"
                    " :now, :now, 'succeeded')"
                ),
                {"now": NOW},
            )
            conn.execute(
                sa_text(
                    "INSERT INTO tweet (x_id, author_handle, text, created_at,"
                    " first_fetched_at, source, is_repost, is_reply, raw) VALUES"
                    " (1, 'reporter', 'some text', :now, :now, 'list', false, false, '{}')"
                ),
                {"now": NOW},
            )

        other_tables = set(SQLModel.metadata.tables.keys()) - {"delivery_log"}
        excluded = DEFAULT_EXCLUDE | {"search_vector"}
        with Session(engine) as session:
            before = table_contents(session, exclude=excluded, tables=other_tables)

        run_alembic(url, "upgrade", "0007")
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert "delivery_log" in inspector.get_table_names()
            unique = {
                tuple(index["column_names"])
                for index in inspector.get_indexes("delivery_log")
                if index["unique"]
            } | {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints("delivery_log")
            }
            assert ("idempotency_key",) in unique
        with Session(engine) as session:
            assert table_contents(session, exclude=excluded, tables=other_tables) == before

        run_alembic(url, "downgrade", "-1")
        with engine.connect() as conn:
            assert "delivery_log" not in inspect(conn).get_table_names()
        with Session(engine) as session:
            assert table_contents(session, exclude=excluded, tables=other_tables) == before
