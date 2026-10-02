import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.alerts.config import AlertSettings, alerts_disabled_reason, parse_alert_config
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
    assert config.slots == (120, 30)
    assert config.trending_min_accounts == 3
    assert config.widely_owned_percent == Decimal("15")
    assert config.rehearsal_deadline is None


def test_values_are_parsed():
    config = parse_alert_config(
        settings(
            alert_slots_minutes="180, 60,15",
            alert_trending_min_accounts="5",
            alert_widely_owned_percent="7.5",
            alert_rehearsal_deadline="2026-10-04 18:00",
        )
    )
    assert config.slots == (180, 60, 15)
    assert config.trending_min_accounts == 5
    assert config.widely_owned_percent == Decimal("7.5")
    assert config.rehearsal_deadline == datetime(2026, 10, 4, 16, 0, tzinfo=UTC)


def test_empty_environment_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("ALERT_SLOTS_MINUTES", "")
    monkeypatch.setenv("ALERT_REHEARSAL_DEADLINE", "")
    config = parse_alert_config(AlertSettings(_env_file=None))
    assert config.slots == (120, 30)
    assert config.rehearsal_deadline is None


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ALERT_SLOTS_MINUTES", "abc"),
        ("ALERT_SLOTS_MINUTES", "0,30"),
        ("ALERT_SLOTS_MINUTES", "30,120"),
        ("ALERT_SLOTS_MINUTES", "120,120"),
        ("ALERT_SLOTS_MINUTES", "120,-5"),
        ("ALERT_TRENDING_MIN_ACCOUNTS", "0"),
        ("ALERT_TRENDING_MIN_ACCOUNTS", "x"),
        ("ALERT_WIDELY_OWNED_PERCENT", "-1"),
        ("ALERT_WIDELY_OWNED_PERCENT", "101"),
        ("ALERT_WIDELY_OWNED_PERCENT", "x"),
        ("ALERTS_ENABLED", "maybe"),
        ("ALERT_REHEARSAL_DEADLINE", "tomorrow"),
    ],
)
def test_invalid_values_name_the_variable(variable, value):
    with pytest.raises(ConfigError, match=variable):
        parse_alert_config(settings(**{variable.lower(): value}))


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
