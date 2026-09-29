from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openrouter import ChatOpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig

from app.extraction.config import PROVIDER, LlmConfig
from app.extraction.model_settings import ModelSettings

_REQUEST_TIMEOUT_MILLISECONDS = 60_000
_NO_RETRIES = RetryConfig("none", BackoffStrategy(0, 0, 1.0, 0), False)


@dataclass(frozen=True)
class ChatModelSpec:
    provider: str
    model: str
    chat_model: BaseChatModel
    structured_kwargs: dict[str, Any] = field(default_factory=dict)
    settings: ModelSettings | None = None


def build_chat_model(config: LlmConfig) -> ChatModelSpec:
    # The `models` list shares one request's parameters between the primary and the fallback,
    # so the fallback runs at the primary's reasoning level and `temperature` is sent only
    # when both rows allow it.
    kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key,
        "timeout": _REQUEST_TIMEOUT_MILLISECONDS,
        "max_retries": 0,
        "reasoning": {"effort": config.settings.reasoning_effort},
        "openrouter_provider": {"require_parameters": True},
    }
    accepts_temperature = config.settings.temperature and (
        config.fallback_settings is None or config.fallback_settings.temperature
    )
    if accepts_temperature:
        kwargs["temperature"] = 0
    if config.fallback_model is not None:
        kwargs["model_kwargs"] = {"models": [config.model, config.fallback_model]}
    chat_model = ChatOpenRouter(**kwargs)
    # With `max_retries=0` ChatOpenRouter passes no retry config, and the SDK then falls back to
    # its own backoff on 5XX for up to an hour; the service owns the attempts instead.
    chat_model.client.sdk_configuration.retry_config = _NO_RETRIES
    return ChatModelSpec(
        provider=PROVIDER,
        model=config.model,
        chat_model=chat_model,
        structured_kwargs={"method": config.settings.structured_method},
        settings=config.settings,
    )
