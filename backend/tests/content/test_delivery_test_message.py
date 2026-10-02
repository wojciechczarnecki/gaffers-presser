from datetime import UTC, datetime

from app.delivery.content import load_test_message


def test_renders_with_time():
    message = load_test_message(datetime(2026, 10, 1, 10, 30, tzinfo=UTC))
    assert message.title.strip()
    assert "2026-10-01 12:30" in message.text
    assert "{time}" not in message.text
