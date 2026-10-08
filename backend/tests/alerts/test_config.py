import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.alerts.config import (
    AlertSettings,
    WallClockSlot,
    alerts_disabled_reason,
    format_slots,
    parse_alert_config,
)
from app.core.errors import ConfigError


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    import os

    for name in list(os.environ):
        if re.match(r"ALERT", name):
            monkeypatch.delenv(name)


def settings(**values) -> AlertSettings:
    return AlertSettings(_env_file=None, **values)


def test_defaults():
    config = parse_alert_config(settings())
    assert config.slots == (WallClockSlot(1, time(20, 0)), 60)
    assert format_slots(config.slots) == "D-1@20:00,60"
    assert config.trending_min_accounts == 3
    assert config.widely_owned_percent == Decimal("15")
    assert config.rehearsal_deadline is None
    assert config.max_lookback == timedelta(days=7)


def test_values_are_parsed():
    config = parse_alert_config(
        settings(
            alert_slots="D-2@18:00, D-1@20:00,120,30",
            alert_trending_min_accounts="5",
            alert_widely_owned_percent="7.5",
            alert_rehearsal_deadline="2026-10-04 18:00",
            alert_max_lookback_days="10",
        )
    )
    assert config.slots == (
        WallClockSlot(2, time(18, 0)),
        WallClockSlot(1, time(20, 0)),
        120,
        30,
    )
    assert config.trending_min_accounts == 5
    assert config.widely_owned_percent == Decimal("7.5")
    assert config.rehearsal_deadline == datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
    assert config.max_lookback == timedelta(days=10)


def test_empty_environment_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("ALERT_SLOTS", "")
    monkeypatch.setenv("ALERT_SLOTS_MINUTES", "")
    monkeypatch.setenv("ALERT_REHEARSAL_DEADLINE", "")
    config = parse_alert_config(AlertSettings(_env_file=None))
    assert config.slots == (WallClockSlot(1, time(20, 0)), 60)
    assert config.rehearsal_deadline is None


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ALERT_SLOTS", "abc"),
        ("ALERT_SLOTS", "0"),
        ("ALERT_SLOTS", "0,30"),
        ("ALERT_SLOTS", "D-0@20:00"),
        ("ALERT_SLOTS", "D-1@25:00"),
        ("ALERT_SLOTS", "D-1@20:60"),
        ("ALERT_SLOTS", "D1@20:00"),
        ("ALERT_SLOTS", "30,120"),
        ("ALERT_SLOTS", "120,120"),
        ("ALERT_SLOTS", "120,-5"),
        ("ALERT_SLOTS", "60,D-1@20:00"),
        ("ALERT_TRENDING_MIN_ACCOUNTS", "0"),
        ("ALERT_TRENDING_MIN_ACCOUNTS", "x"),
        ("ALERT_WIDELY_OWNED_PERCENT", "-1"),
        ("ALERT_WIDELY_OWNED_PERCENT", "101"),
        ("ALERT_WIDELY_OWNED_PERCENT", "x"),
        ("ALERTS_ENABLED", "maybe"),
        ("ALERT_REHEARSAL_DEADLINE", "tomorrow"),
        ("ALERT_MAX_LOOKBACK_DAYS", "0"),
        ("ALERT_MAX_LOOKBACK_DAYS", "week"),
    ],
)
def test_invalid_values_name_the_variable(variable, value):
    with pytest.raises(ConfigError, match=variable):
        parse_alert_config(settings(**{variable.lower(): value}))


def test_retired_slots_minutes_variable_stops_the_start():
    with pytest.raises(ConfigError, match="replaced by ALERT_SLOTS"):
        parse_alert_config(settings(alert_slots_minutes="120,30"))


def test_alert_slots_defaults_and_mixed_values():
    assert parse_alert_config(settings(alert_slots="")).slots == (WallClockSlot(1, time(20, 0)), 60)
    config = parse_alert_config(settings(alert_slots="D-1@8:05, D-1@20:00"))
    assert config.slots == (WallClockSlot(1, time(8, 5)), WallClockSlot(1, time(20, 0)))
    assert format_slots(config.slots) == "D-1@08:05,D-1@20:00"


def test_alerts_disabled_reasons():
    def reason(*, delivery=True, tweet_ingest=True, extraction=True, **values):
        return alerts_disabled_reason(
            settings(**values),
            delivery=delivery,
            tweet_ingest=tweet_ingest,
            extraction=extraction,
        )

    assert reason() is None
    assert reason(delivery=False) == "delivery disabled"
    assert reason(tweet_ingest=False) == "tweet ingest disabled"
    assert reason(extraction=False) == "extraction disabled"
    assert reason(alerts_enabled="false") == "ALERTS_ENABLED=false"
    assert reason(alerts_enabled="false", delivery=False) == "ALERTS_ENABLED=false"
    assert reason(alerts_enabled="TRUE") is None
