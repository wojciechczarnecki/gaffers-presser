import dataclasses

import pytest

from app.delivery.channels.base import Channel, InvalidMessageError, Message


class RecordingChannel:
    name = "recording"

    def __init__(self) -> None:
        self.received: list[Message] = []

    def send(self, message: Message, idempotency_key: str) -> str | None:
        self.received.append(message)
        return None

    def close(self) -> None:
        pass


def test_message_shape_and_channel_protocol():
    assert [f.name for f in dataclasses.fields(Message)] == ["title", "text", "html"]
    message = Message(title="Tytul", text="Tresc")
    assert message.html is None

    channel: Channel = RecordingChannel()
    assert channel.send(message, "test:k") is None
    assert channel.received[0] is message


@pytest.mark.parametrize(
    ("title", "text"),
    [("", "body"), ("   ", "body"), ("title", ""), ("title", " \n\t "), ("", "")],
)
def test_empty_title_or_text_is_rejected(title, text):
    with pytest.raises(InvalidMessageError):
        Message(title=title, text=text)
