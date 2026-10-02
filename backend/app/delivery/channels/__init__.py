from app.delivery.channels.base import Channel
from app.delivery.channels.resend import ResendChannel
from app.delivery.config import DeliveryConfig


def build_channel(config: DeliveryConfig) -> Channel:
    settings = config.settings
    assert settings.resend_api_key is not None
    return ResendChannel(
        settings.resend_api_key.get_secret_value(),
        settings.delivery_email_from,
        settings.delivery_email_to,
    )
