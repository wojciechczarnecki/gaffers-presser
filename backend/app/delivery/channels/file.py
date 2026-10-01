import secrets
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from app.core.clock import Clock
from app.delivery.channels.base import Message

SENDER = "presser@localhost.invalid"
RECIPIENT = "owner@localhost.invalid"


class FileChannel:
    name = "file"

    def __init__(self, directory: Path, clock: Clock) -> None:
        self._directory = directory
        self._clock = clock

    def close(self) -> None:
        pass

    def send(self, message: Message, idempotency_key: str) -> str | None:
        now = self._clock.now()
        mail = EmailMessage()
        mail["From"] = SENDER
        mail["To"] = RECIPIENT
        mail["Subject"] = message.title
        mail["Date"] = format_datetime(now)
        mail["Message-ID"] = make_msgid(domain="localhost.invalid")
        mail.set_content(message.text)
        if message.html is not None:
            mail.add_alternative(message.html, subtype="html")
        else:
            mail.make_alternative()

        self._directory.mkdir(parents=True, exist_ok=True)
        name = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(4)}.eml"
        (self._directory / name).write_bytes(mail.as_bytes())
        return None
