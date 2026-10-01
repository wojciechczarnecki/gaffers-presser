import email
import email.policy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.delivery.channels.base import Message
from app.delivery.channels.file import FileChannel

START = datetime(2026, 10, 1, 12, 30, 15, 250000, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def sleep(self, seconds: float) -> None:
        pass


def parse(path: Path):
    return email.message_from_bytes(path.read_bytes(), policy=email.policy.default)


def test_returns_no_provider_id(tmp_path):
    channel = FileChannel(tmp_path, FixedClock(START))
    assert channel.send(Message(title="T", text="B")) is None
    assert len(list(tmp_path.glob("*.eml"))) == 1


def test_writes_multipart_eml_with_text_part(tmp_path):
    message = Message(title="Konferencja po kolejce", text="Zażółć gęślą jaźń\nDruga linia")
    FileChannel(tmp_path, FixedClock(START)).send(message)

    (path,) = tmp_path.glob("*.eml")
    parsed = parse(path)
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed["Subject"] == message.title
    assert parsed["Date"] is not None
    assert parsed["Message-ID"] is not None
    text = parsed.get_body(("plain",)).get_content()
    assert text == message.text + "\n"
    assert parsed.get_body(("html",)) is None


def test_html_part_when_present(tmp_path):
    message = Message(title="T", text="Tresc", html="<p>Tresc żółta</p>")
    FileChannel(tmp_path, FixedClock(START)).send(message)

    (path,) = tmp_path.glob("*.eml")
    parsed = parse(path)
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed.get_body(("plain",)).get_content() == "Tresc\n"
    assert parsed.get_body(("html",)).get_content().strip() == message.html


def test_files_sort_by_send_time(tmp_path):
    clock = FixedClock(START)
    channel = FileChannel(tmp_path, clock)
    for index in range(3):
        channel.send(Message(title=f"Title {index}", text="Body"))
        clock.advance(timedelta(seconds=1))

    names = sorted(path.name for path in tmp_path.glob("*.eml"))
    assert [parse(tmp_path / name)["Subject"] for name in names] == [
        "Title 0",
        "Title 1",
        "Title 2",
    ]
    assert names[0].startswith("20261001T123015250000Z-")


def test_same_instant_sends_do_not_overwrite(tmp_path):
    channel = FileChannel(tmp_path, FixedClock(START))
    channel.send(Message(title="A", text="B"))
    channel.send(Message(title="A", text="B"))
    assert len(list(tmp_path.glob("*.eml"))) == 2


def test_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "outbox"
    FileChannel(target, FixedClock(START)).send(Message(title="T", text="B"))
    assert len(list(target.glob("*.eml"))) == 1
