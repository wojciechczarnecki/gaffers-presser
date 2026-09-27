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


def test_development_section_lists_commands():
    section = _development_section()
    assert "docker compose up -d" in section
    assert "alembic upgrade head" in section
    for command in COMMANDS:
        assert command in section
