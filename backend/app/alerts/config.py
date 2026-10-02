from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError
from app.core.local_time import parse_local

DEFAULT_SLOTS = "120,30"
DEFAULT_TRENDING_MIN_ACCOUNTS = "3"
DEFAULT_WIDELY_OWNED_PERCENT = "15"


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    alerts_enabled: str = "true"
    alert_slots_minutes: str = DEFAULT_SLOTS
    alert_trending_min_accounts: str = DEFAULT_TRENDING_MIN_ACCOUNTS
    alert_widely_owned_percent: str = DEFAULT_WIDELY_OWNED_PERCENT
    alert_rehearsal_deadline: str = ""


@dataclass(frozen=True)
class AlertConfig:
    slots: tuple[int, ...]
    trending_min_accounts: int
    widely_owned_percent: Decimal
    rehearsal_deadline: datetime | None


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


def _slots(value: str) -> tuple[int, ...]:
    slots = tuple(
        _positive_int("ALERT_SLOTS_MINUTES", part)
        for part in (value.strip() or DEFAULT_SLOTS).split(",")
    )
    if any(later >= earlier for earlier, later in zip(slots, slots[1:], strict=False)):
        raise ConfigError("ALERT_SLOTS_MINUTES must be strictly decreasing minutes before deadline")
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
    return AlertConfig(
        slots=_slots(settings.alert_slots_minutes),
        trending_min_accounts=_positive_int(
            "ALERT_TRENDING_MIN_ACCOUNTS",
            settings.alert_trending_min_accounts or DEFAULT_TRENDING_MIN_ACCOUNTS,
        ),
        widely_owned_percent=_percent(settings.alert_widely_owned_percent),
        rehearsal_deadline=_rehearsal(settings.alert_rehearsal_deadline),
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
