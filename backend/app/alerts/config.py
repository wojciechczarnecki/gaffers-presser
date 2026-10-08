import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError
from app.core.local_time import parse_local
from app.fpl.deadlines import DEFAULT_MAX_LOOKBACK

DEFAULT_SLOTS = "D-1@20:00,60"
_WALL_CLOCK = re.compile(r"D-(\d+)@(\d{1,2}):(\d{2})")
DEFAULT_TRENDING_MIN_ACCOUNTS = "3"
DEFAULT_WIDELY_OWNED_PERCENT = "15"
DEFAULT_MAX_LOOKBACK_DAYS = str(DEFAULT_MAX_LOOKBACK.days)


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    alerts_enabled: str = "true"
    alert_slots: str = DEFAULT_SLOTS
    alert_slots_minutes: str = ""
    alert_trending_min_accounts: str = DEFAULT_TRENDING_MIN_ACCOUNTS
    alert_widely_owned_percent: str = DEFAULT_WIDELY_OWNED_PERCENT
    alert_rehearsal_deadline: str = ""
    alert_max_lookback_days: str = DEFAULT_MAX_LOOKBACK_DAYS


@dataclass(frozen=True)
class WallClockSlot:
    days_before: int
    at: time

    def __str__(self) -> str:
        return f"D-{self.days_before}@{self.at:%H:%M}"


Slot = int | WallClockSlot


def format_slots(slots: tuple[Slot, ...]) -> str:
    return ",".join(str(slot) for slot in slots)


@dataclass(frozen=True)
class AlertConfig:
    slots: tuple[Slot, ...]
    trending_min_accounts: int
    widely_owned_percent: Decimal
    rehearsal_deadline: datetime | None
    # the alert window starts at the previous deadline, but never earlier than this before now
    max_lookback: timedelta = DEFAULT_MAX_LOOKBACK


def _enabled(settings: AlertSettings) -> bool:
    value = settings.alerts_enabled.strip().lower() or "true"
    if value not in ("true", "false"):
        raise ConfigError("ALERTS_ENABLED must be true or false")
    return value == "true"


def _positive_int(variable: str, value: str) -> int:
    try:
        number = int(value.strip())
    except ValueError:
        raise ConfigError(f"{variable} must be a positive integer") from None
    if number < 1:
        raise ConfigError(f"{variable} must be a positive integer")
    return number


def _slot(part: str) -> Slot:
    text = part.strip()
    match = _WALL_CLOCK.fullmatch(text)
    if match is None:
        return _positive_int("ALERT_SLOTS", text)
    days, hours, minutes = (int(group) for group in match.groups())
    if days < 1 or hours > 23 or minutes > 59:
        raise ConfigError(f"ALERT_SLOTS has an invalid slot {text!r}")
    return WallClockSlot(days, time(hours, minutes))


def _slots(value: str) -> tuple[Slot, ...]:
    slots = tuple(_slot(part) for part in (value.strip() or DEFAULT_SLOTS).split(","))
    numbers = [slot for slot in slots if isinstance(slot, int)]
    first_minutes = next((i for i, slot in enumerate(slots) if isinstance(slot, int)), len(slots))
    if any(isinstance(slot, WallClockSlot) for slot in slots[first_minutes:]):
        raise ConfigError("ALERT_SLOTS must not place a wall-clock slot after a minutes slot")
    if any(later >= earlier for earlier, later in zip(numbers, numbers[1:], strict=False)):
        raise ConfigError("ALERT_SLOTS must be strictly decreasing minutes before deadline")
    return slots


def _percent(value: str) -> Decimal:
    try:
        percent = Decimal(value.strip() or DEFAULT_WIDELY_OWNED_PERCENT)
    except InvalidOperation:
        raise ConfigError("ALERT_WIDELY_OWNED_PERCENT must be a number from 0 to 100") from None
    if not percent.is_finite() or not Decimal(0) <= percent <= Decimal(100):
        raise ConfigError("ALERT_WIDELY_OWNED_PERCENT must be a number from 0 to 100")
    return percent


def _rehearsal(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        return parse_local(value.strip())
    except ValueError:
        raise ConfigError(
            "ALERT_REHEARSAL_DEADLINE must be a Warsaw time like 2026-10-04 18:00"
        ) from None


def parse_alert_config(settings: AlertSettings) -> AlertConfig:
    _enabled(settings)
    if settings.alert_slots_minutes.strip():
        raise ConfigError("ALERT_SLOTS_MINUTES was replaced by ALERT_SLOTS; remove it")
    return AlertConfig(
        slots=_slots(settings.alert_slots),
        trending_min_accounts=_positive_int(
            "ALERT_TRENDING_MIN_ACCOUNTS",
            settings.alert_trending_min_accounts or DEFAULT_TRENDING_MIN_ACCOUNTS,
        ),
        widely_owned_percent=_percent(settings.alert_widely_owned_percent),
        rehearsal_deadline=_rehearsal(settings.alert_rehearsal_deadline),
        max_lookback=timedelta(
            days=_positive_int(
                "ALERT_MAX_LOOKBACK_DAYS",
                settings.alert_max_lookback_days or DEFAULT_MAX_LOOKBACK_DAYS,
            )
        ),
    )


def alerts_disabled_reason(
    settings: AlertSettings, *, delivery: bool, tweet_ingest: bool, extraction: bool
) -> str | None:
    if not _enabled(settings):
        return "ALERTS_ENABLED=false"
    if not delivery:
        return "delivery disabled"
    if not tweet_ingest:
        return "tweet ingest disabled"
    if not extraction:
        return "extraction disabled"
    return None
