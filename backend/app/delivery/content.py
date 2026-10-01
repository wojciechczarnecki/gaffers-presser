import tomllib
from datetime import datetime
from pathlib import Path

from app.core.local_time import format_local
from app.delivery.channels.base import Message

TEST_MESSAGE_PATH = (
    Path(__file__).resolve().parent.parent / "content" / "delivery_test_message.toml"
)


def load_test_message(now: datetime) -> Message:
    content = tomllib.loads(TEST_MESSAGE_PATH.read_text(encoding="utf-8"))
    return Message(
        title=content["title"],
        text=content["text"].replace("{time}", format_local(now)),
    )
