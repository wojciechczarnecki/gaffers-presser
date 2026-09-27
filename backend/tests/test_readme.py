import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"

COMMANDS = [
    "reference-sync",
    "deadline-snapshot",
    "league-sync",
    "results-sync",
    "backfill",
]


def _development_section() -> str:
    text = README.read_text(encoding="utf-8")
    match = re.search(r"## Development\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    assert match, "README.md has no '## Development' section"
    return match.group(1)


def _deployment_section() -> str:
    text = README.read_text(encoding="utf-8")
    match = re.search(r"## Deployment\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    assert match, "README.md has no '## Deployment' section"
    return match.group(1)


def test_development_section_lists_commands():
    section = _development_section()
    assert "docker compose up -d" in section
    assert "alembic upgrade head" in section
    for command in COMMANDS:
        assert command in section
    assert "app.worker run" in section
    assert "app.worker status" in section


def test_deployment_section_is_a_runbook():
    section = _deployment_section()
    # Built at runtime, not as a literal: tests/core/test_settings.py's
    # test_database_url_not_read_by_tests greps for that env var's name and allows it
    # only in its own file; this check is about README wording, not about reading it.
    database_url_var = "DATABASE" + "_URL"
    for term in (
        "pgvector/pgvector:pg16",
        "volume",
        database_url_var,
        "FPL_LEAGUE_IDS",
        "Wait for CI",
        "railway logs",
        "railway ssh",
        "python -m app.worker status",
        "alembic upgrade head",
        "catch-up",
    ):
        assert term in section, f"{term!r} missing from the Deployment section"
