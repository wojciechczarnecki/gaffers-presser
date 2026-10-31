from datetime import UTC, datetime, timedelta

import pytest

from app.extraction.lineups import kept_at_read

POSTED = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("event_type", "certainty", "lead_hours", "answer", "kept"),
    [
        ("out", "confirmed", 10, False, False),
        ("doubt", "rumour", 100, False, False),
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


@pytest.mark.parametrize("answer", [None, True, False])
@pytest.mark.parametrize(
    "event_type", ["out", "doubt", "benched", "confirmed_starter", "available"]
)
def test_kept_at_read_by_type_and_answer(event_type, answer):
    next_kickoff = POSTED + timedelta(hours=10)
    kept = kept_at_read(event_type, "likely", POSTED, next_kickoff, answer)
    assert kept is not (answer is False and event_type != "available")


@pytest.mark.parametrize("answer", [None, True, False])
def test_an_available_event_70_hours_before_kickoff_is_kept(answer):
    next_kickoff = POSTED + timedelta(hours=70)
    assert kept_at_read("available", "confirmed", POSTED, next_kickoff, answer) is True
