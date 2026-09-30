from datetime import UTC, datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")


def parse_local(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(UTC)


def format_local(moment: datetime) -> str:
    return moment.astimezone(WARSAW).strftime("%Y-%m-%d %H:%M")
