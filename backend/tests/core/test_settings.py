import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.core.errors import ConfigError
from app.core.settings import Settings, parse_league_ids
from app.fpl.cli import app

REPO_ROOT = Path(__file__).resolve().parents[3]


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
