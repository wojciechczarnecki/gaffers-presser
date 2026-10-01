from pathlib import Path

from app.core.clock import Clock
from app.delivery.channels.base import Channel
from app.delivery.channels.file import FileChannel
from app.delivery.channels.resend import ResendChannel
from app.delivery.config import DeliveryConfig


def build_channel(config: DeliveryConfig, clock: Clock) -> Channel:
    settings = config.settings
    if config.provider == "resend":
        assert settings.resend_api_key is not None
        return ResendChannel(
            settings.resend_api_key.get_secret_value(),
            settings.delivery_email_from,
            settings.delivery_email_to,
        )
    return FileChannel(Path(settings.delivery_file_dir).resolve(), clock)
