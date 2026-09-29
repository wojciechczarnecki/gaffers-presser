from typing import TypeVar

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    openrouter_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    usd_pln_rate: float | None = None


T = TypeVar("T", bound=LlmSettings)


def load_llm_settings(cls: type[T]) -> T:
    try:
        return cls()
    except ValidationError as exc:
        variables = sorted({str(error["loc"][0]).upper() for error in exc.errors() if error["loc"]})
        raise ConfigError(f"invalid value of {', '.join(variables)}") from None
