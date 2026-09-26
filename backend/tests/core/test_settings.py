import subprocess
from pathlib import Path

import pytest

from app.core.errors import ConfigError
from app.core.settings import Settings, parse_league_ids

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
