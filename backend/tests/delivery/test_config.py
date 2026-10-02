import re

import pytest

from app.core.errors import ConfigError
from app.delivery.channels import build_channel
from app.delivery.channels.resend import ResendChannel
from app.delivery.config import DeliverySettings, resolve_delivery

SECRET = "re_synthetic_secret_value_123"
RECIPIENT = "owner@example.test"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    import os

    for name in list(os.environ):
        if re.match(r"(DELIVERY_|RESEND_)", name):
            monkeypatch.delenv(name)


def settings(**values) -> DeliverySettings:
    return DeliverySettings(_env_file=None, **values)


def test_provider_selects_or_disables():
    assert resolve_delivery(settings()) is None
    assert resolve_delivery(settings(delivery_provider="")) is None

    resend_config = resolve_delivery(
        settings(delivery_provider="resend", resend_api_key=SECRET, delivery_email_to=RECIPIENT)
    )
    assert resend_config is not None and resend_config.provider == "resend"
    channel = build_channel(resend_config)
    assert isinstance(channel, ResendChannel)
    channel.close()


def test_unknown_provider_names_the_variable():
    with pytest.raises(ConfigError) as info:
        resolve_delivery(settings(delivery_provider="smtp"))
    message = str(info.value)
    assert "DELIVERY_PROVIDER" in message
    assert "resend" in message


def test_resend_requires_key_and_recipient():
    with pytest.raises(ConfigError) as info:
        resolve_delivery(settings(delivery_provider="resend", delivery_email_to=RECIPIENT))
    assert "RESEND_API_KEY" in str(info.value)
    assert RECIPIENT not in str(info.value)

    with pytest.raises(ConfigError) as info:
        resolve_delivery(settings(delivery_provider="resend", resend_api_key=SECRET))
    assert "DELIVERY_EMAIL_TO" in str(info.value)
    assert SECRET not in str(info.value)


def test_resend_from_defaults_to_testing_domain():
    config = resolve_delivery(
        settings(delivery_provider="resend", resend_api_key=SECRET, delivery_email_to=RECIPIENT)
    )
    assert config is not None
    assert config.settings.delivery_email_from == "onboarding@resend.dev"

    custom = resolve_delivery(
        settings(
            delivery_provider="resend",
            resend_api_key=SECRET,
            delivery_email_to=RECIPIENT,
            delivery_email_from="presser@example.test",
        )
    )
    assert custom is not None
    assert custom.settings.delivery_email_from == "presser@example.test"
