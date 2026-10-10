from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openrouter import ChatOpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig
from pydantic import SecretStr

from app.core.errors import ConfigError
from app.llm.models import ModelSettings, load_model_settings, pair_compatible
from app.llm.settings import LlmSettings, load_llm_settings

PROVIDER = "openrouter"

_REQUEST_TIMEOUT_MILLISECONDS = 60_000
_NO_RETRIES = RetryConfig("none", BackoffStrategy(0, 0, 1.0, 0), False)


class ChatSettings(LlmSettings):
    llm_model: str = ""
    llm_fallback_model: str = ""


def load_chat_settings() -> ChatSettings:
    return load_llm_settings(ChatSettings)


# Chosen in ADR 0006 from the test-split runs (selection rule of SPEC 005, AC17/AC18).
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_FALLBACK_MODEL = "google/gemini-3.1-flash-lite"


@dataclass(frozen=True)
class LlmConfig:
    model: str
    fallback_model: str | None
    api_key: SecretStr
    settings: ModelSettings
    fallback_settings: ModelSettings | None


def _catalogue_row(catalogue: dict[str, ModelSettings], model: str, variable: str) -> ModelSettings:
    row = catalogue.get(model)
    if row is None:
        raise ConfigError(f"{variable} names a model with no entry in model_settings.toml")
    return row


def _load_catalogue(catalogue: dict[str, ModelSettings] | None) -> dict[str, ModelSettings]:
    if catalogue is not None:
        return catalogue
    try:
        return load_model_settings()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"model_settings.toml cannot be loaded: {type(exc).__name__}") from None


def single_model_config(
    api_key: SecretStr,
    model: str,
    catalogue: dict[str, ModelSettings] | None = None,
    variable: str = "--model",
) -> LlmConfig:
    row = _catalogue_row(_load_catalogue(catalogue), model, variable)
    return LlmConfig(
        model=model,
        fallback_model=None,
        api_key=api_key,
        settings=row,
        fallback_settings=None,
    )


def structured_kwargs_for(
    model: str, catalogue: dict[str, ModelSettings] | None = None
) -> dict[str, Any]:
    try:
        row = _load_catalogue(catalogue).get(model)
    except ConfigError:
        return {}
    return {"method": row.structured_method} if row is not None else {}


def resolve_llm(
    settings: ChatSettings,
    model: str | None = None,
    use_fallback: bool = True,
    catalogue: dict[str, ModelSettings] | None = None,
) -> LlmConfig | None:
    if settings.openrouter_api_key is None:
        return None
    catalogue = _load_catalogue(catalogue)

    if model:
        primary, primary_variable = model, "--model"
    elif settings.llm_model:
        primary, primary_variable = settings.llm_model, "LLM_MODEL"
    else:
        primary, primary_variable = DEFAULT_MODEL, "DEFAULT_MODEL"
    primary_row = _catalogue_row(catalogue, primary, primary_variable)

    fallback: str | None = None
    fallback_row: ModelSettings | None = None
    if use_fallback:
        if settings.llm_fallback_model:
            candidate, fallback_variable = settings.llm_fallback_model, "LLM_FALLBACK_MODEL"
        else:
            candidate, fallback_variable = DEFAULT_FALLBACK_MODEL, "DEFAULT_FALLBACK_MODEL"
        if candidate and candidate != primary:
            candidate_row = _catalogue_row(catalogue, candidate, fallback_variable)
            if pair_compatible(primary, candidate, catalogue):
                fallback, fallback_row = candidate, candidate_row
            elif fallback_variable == "LLM_FALLBACK_MODEL":
                raise ConfigError(
                    "LLM_FALLBACK_MODEL cannot answer for the primary model: their"
                    " structured_method or reasoning_effort in model_settings.toml differ"
                )

    return LlmConfig(
        model=primary,
        fallback_model=fallback,
        api_key=settings.openrouter_api_key,
        settings=primary_row,
        fallback_settings=fallback_row,
    )


@dataclass(frozen=True)
class ChatModelSpec:
    provider: str
    model: str
    chat_model: BaseChatModel
    structured_kwargs: dict[str, Any] = field(default_factory=dict)
    settings: ModelSettings | None = None


def build_chat_model(config: LlmConfig, temperature: float = 0.0) -> ChatModelSpec:
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
        kwargs["temperature"] = temperature
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
