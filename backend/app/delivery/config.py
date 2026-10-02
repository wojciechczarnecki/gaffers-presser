from dataclasses import dataclass

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError

PROVIDERS = ("resend",)
DEFAULT_EMAIL_FROM = "onboarding@resend.dev"


class DeliverySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    delivery_provider: str = ""
    resend_api_key: SecretStr | None = None
    delivery_email_to: str = ""
    delivery_email_from: str = DEFAULT_EMAIL_FROM


@dataclass(frozen=True)
class DeliveryConfig:
    provider: str
    settings: DeliverySettings


def resolve_delivery(settings: DeliverySettings) -> DeliveryConfig | None:
    provider = settings.delivery_provider
    if not provider:
        return None
    if provider not in PROVIDERS:
        raise ConfigError(
            f"DELIVERY_PROVIDER must be one of: {', '.join(PROVIDERS)} (empty disables delivery)"
        )
    if settings.resend_api_key is None:
        raise ConfigError("RESEND_API_KEY must be set for DELIVERY_PROVIDER=resend")
    if not settings.delivery_email_to:
        raise ConfigError("DELIVERY_EMAIL_TO must be set for DELIVERY_PROVIDER=resend")
    return DeliveryConfig(provider, settings)
