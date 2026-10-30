import pytest
from pydantic import ValidationError

from app.extraction.schemas import ExtractedEvent


def test_available_is_a_valid_event_type():
    event = ExtractedEvent(player="Palmer", event_type="available", certainty="confirmed")
    assert event.event_type == "available"


def test_unknown_event_type_fails_validation():
    with pytest.raises(ValidationError):
        ExtractedEvent(player="Palmer", event_type="injured", certainty="confirmed")
