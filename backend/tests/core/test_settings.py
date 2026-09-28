import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from app.core.errors import ConfigError
from app.core.settings import Settings, normalize_database_url, parse_league_ids
from app.fpl.cli import app
from app.worker.cli import app as worker_app
from tests.conftest import BACKEND_DIR

REPO_ROOT = Path(__file__).resolve().parents[3]


def _with_scheme(url: str, scheme: str) -> str:
    return url.replace("postgresql+psycopg://", f"{scheme}://", 1)


def test_parse_league_ids_valid():
    assert parse_league_ids("1, 2") == [1, 2]


def test_parse_league_ids_empty():
    with pytest.raises(ConfigError, match="FPL_LEAGUE_IDS"):
        parse_league_ids("")


def test_parse_league_ids_whitespace():
    with pytest.raises(ConfigError, match="FPL_LEAGUE_IDS"):
        parse_league_ids("   ")


def test_parse_league_ids_double_comma():
    with pytest.raises(ConfigError, match="FPL_LEAGUE_IDS"):
        parse_league_ids("1,,2")


def test_parse_league_ids_non_numeric():
    with pytest.raises(ConfigError, match="FPL_LEAGUE_IDS"):
        parse_league_ids("1,abc")


def test_parse_league_ids_negative():
    with pytest.raises(ConfigError, match="FPL_LEAGUE_IDS"):
        parse_league_ids("-3")


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_normalize_database_url_accepts_postgres_schemes(scheme):
    url = normalize_database_url(f"{scheme}://u:p@localhost:5432/d")
    assert url == "postgresql+psycopg://u:p@localhost:5432/d"


def test_normalize_database_url_rejects_other_scheme():
    with pytest.raises(ConfigError, match="DATABASE_URL must be a PostgreSQL URL"):
        normalize_database_url("mysql://u:p@localhost/d")


def test_normalize_database_url_rejects_malformed_url():
    with pytest.raises(ConfigError, match="DATABASE_URL must be a PostgreSQL URL"):
        normalize_database_url("not a url")


@pytest.mark.parametrize("raw", ["", "   "])
def test_normalize_database_url_rejects_empty(raw):
    with pytest.raises(ConfigError, match="DATABASE_URL must be set"):
        normalize_database_url(raw)


def test_normalize_database_url_error_does_not_contain_the_password():
    with pytest.raises(ConfigError) as exc_info:
        normalize_database_url("mysql://u:secret@localhost/d")
    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_database_url_schemes_connect_through_cli(postgres_url, monkeypatch, scheme):
    monkeypatch.setenv("DATABASE_URL", _with_scheme(postgres_url, scheme))

    from app.fpl.cli import _deps_from_settings

    deps = _deps_from_settings()
    with deps.engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_database_url_schemes_connect_through_alembic(db_engine, postgres_url, scheme):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["DATABASE_URL"] = _with_scheme(postgres_url, scheme)
    alembic = str(Path(sys.executable).parent / "alembic")
    result = subprocess.run(
        [alembic, "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/d")
    monkeypatch.setenv("FPL_LEAGUE_IDS", "1,2")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/d"
    assert settings.fpl_league_ids == "1,2"


def test_database_url_not_read_by_tests():
    result = subprocess.run(
        [
            "grep",
            "-rl",
            "--include=*.py",
            "DATABASE_URL",
            str(REPO_ROOT / "backend" / "tests"),
        ],
        capture_output=True,
        text=True,
    )
    hits = [line for line in result.stdout.splitlines() if line]
    allowed = {str(Path(__file__).resolve())}
    assert set(hits) <= allowed


def test_missing_database_url_is_a_clean_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("FPL_LEAGUE_IDS", "987654301")

    result = CliRunner().invoke(app, ["league-sync", "--gameweek", "1"])

    assert result.exit_code == 1
    assert result.stderr.strip() == "error: DATABASE_URL must be set"
    assert "987654301" not in result.stderr


def test_worker_rejects_missing_database_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("FPL_LEAGUE_IDS", "987654301")

    result = CliRunner().invoke(worker_app, ["run"])

    assert result.exit_code != 0
    assert "DATABASE_URL" in result.stderr
    assert "987654301" not in result.stderr


def test_worker_rejects_malformed_database_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "mysql://u:secret@localhost/d")
    monkeypatch.setenv("FPL_LEAGUE_IDS", "987654301")

    result = CliRunner().invoke(worker_app, ["run"])

    assert result.exit_code != 0
    assert "DATABASE_URL" in result.stderr
    assert "secret" not in result.stderr


def test_worker_rejects_empty_league_ids(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/d")
    monkeypatch.setenv("FPL_LEAGUE_IDS", "")

    result = CliRunner().invoke(worker_app, ["run"])

    assert result.exit_code != 0
    assert "FPL_LEAGUE_IDS" in result.stderr


def test_worker_rejects_malformed_league_ids(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/d")
    monkeypatch.setenv("FPL_LEAGUE_IDS", "1, abc")

    result = CliRunner().invoke(worker_app, ["run"])

    assert result.exit_code != 0
    assert "FPL_LEAGUE_IDS" in result.stderr


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_database_url_schemes_connect_through_worker(
    db_engine, postgres_url, monkeypatch, scheme, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", _with_scheme(postgres_url, scheme))
    monkeypatch.setenv("FPL_LEAGUE_IDS", "1")

    result = CliRunner().invoke(worker_app, ["status"])

    assert result.exit_code == 0


def test_help_and_usage_errors_need_no_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    commands = ("reference-sync", "deadline-snapshot", "league-sync", "results-sync", "backfill")
    for command in commands:
        assert command in result.stdout

    assert runner.invoke(app, ["league-sync", "--help"]).exit_code == 0

    result = runner.invoke(app, ["league-sync"])
    assert result.exit_code == 2
    assert "DATABASE_URL" not in result.stderr


def test_extraction_cli_without_database_url_leaves_the_engine_unset(monkeypatch, tmp_path):
    # `evaluate` needs no database; the other extraction commands say so through db_engine.
    from app.extraction.cli import _deps_from_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    deps = _deps_from_settings()

    assert deps.engine is None
