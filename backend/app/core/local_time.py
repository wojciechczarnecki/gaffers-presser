from datetime import UTC, datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_local(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(UTC)


def format_local(moment: datetime) -> str:
    return moment.astimezone(WARSAW).strftime("%Y-%m-%d %H:%M")


def format_local_day(moment: datetime) -> str:
    local = moment.astimezone(WARSAW)
    return f"{WEEKDAYS[local.weekday()]} {local:%Y-%m-%d %H:%M}"
