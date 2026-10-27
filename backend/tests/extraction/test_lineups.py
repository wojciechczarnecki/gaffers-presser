from datetime import UTC, datetime, timedelta

import pytest

from app.extraction.lineups import kept_at_read

POSTED = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("event_type", "certainty", "lead_hours", "answer", "kept"),
    [
        ("out", "confirmed", 10, False, True),
        ("doubt", "rumour", 100, False, True),
        ("benched", "likely", 10, False, False),
        ("confirmed_starter", "rumour", 10, False, False),
        ("confirmed_starter", "confirmed", 61, True, False),
        ("confirmed_starter", "confirmed", 10, None, True),
        ("confirmed_starter", "confirmed", 10, True, True),
        ("benched", "likely", 100, None, True),
        ("benched", "likely", 100, True, True),
    ],
)
def test_kept_at_read(event_type, certainty, lead_hours, answer, kept):
    next_kickoff = POSTED + timedelta(hours=lead_hours)
    assert kept_at_read(event_type, certainty, POSTED, next_kickoff, answer) is kept


def test_a_lineup_with_no_known_kickoff_is_kept_unless_the_check_said_no():
    assert kept_at_read("benched", "confirmed", POSTED, None, None) is True
    assert kept_at_read("benched", "confirmed", POSTED, None, False) is False
