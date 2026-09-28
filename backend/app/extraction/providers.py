from dataclasses import dataclass, field
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.extraction.config import LlmConfig

_REQUEST_TIMEOUT_SECONDS = 60.0
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model names (or, for OpenRouter, the part after "openai/") starting with any of these
# prefixes reject an explicit `temperature` argument other than their own default.
NO_TEMPERATURE_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")


@dataclass(frozen=True)
class ChatModelSpec:
    provider: str
    model: str
    chat_model: BaseChatModel
    structured_kwargs: dict[str, Any] = field(default_factory=dict)


def _bare_model_name(provider: str, model: str) -> str:
    if provider == "openrouter" and model.startswith("openai/"):
        return model[len("openai/") :]
    return model


def _accepts_temperature(provider: str, model: str) -> bool:
    bare = _bare_model_name(provider, model)
    return not bare.startswith(NO_TEMPERATURE_PREFIXES)


def build_chat_model(config: LlmConfig) -> ChatModelSpec:
    if config.provider == "google":
        kwargs: dict[str, Any] = {
            "model": config.model,
            "google_api_key": config.api_key,
            "max_retries": 0,
            "timeout": _REQUEST_TIMEOUT_SECONDS,
        }
        if _accepts_temperature(config.provider, config.model):
            kwargs["temperature"] = 0
        chat_model: BaseChatModel = ChatGoogleGenerativeAI(**kwargs)
        return ChatModelSpec(config.provider, config.model, chat_model)

    if config.provider == "anthropic":
        kwargs = {
            "model": config.model,
            "anthropic_api_key": config.api_key,
            "max_retries": 0,
            "default_request_timeout": _REQUEST_TIMEOUT_SECONDS,
        }
        if _accepts_temperature(config.provider, config.model):
            kwargs["temperature"] = 0
        chat_model = ChatAnthropic(**kwargs)
        return ChatModelSpec(config.provider, config.model, chat_model)

    if config.provider == "openai":
        kwargs = {
            "model_name": config.model,
            "openai_api_key": config.api_key,
            "max_retries": 0,
            "request_timeout": _REQUEST_TIMEOUT_SECONDS,
        }
        if _accepts_temperature(config.provider, config.model):
            kwargs["temperature"] = 0
        chat_model = ChatOpenAI(**kwargs)
        return ChatModelSpec(config.provider, config.model, chat_model)

    if config.provider == "openrouter":
        kwargs = {
            "model_name": config.model,
            "openai_api_key": config.api_key,
            "openai_api_base": _OPENROUTER_BASE_URL,
            "max_retries": 0,
            "request_timeout": _REQUEST_TIMEOUT_SECONDS,
        }
        if _accepts_temperature(config.provider, config.model):
            kwargs["temperature"] = 0
        chat_model = ChatOpenAI(**kwargs)
        return ChatModelSpec(
            config.provider,
            config.model,
            chat_model,
            structured_kwargs={"method": "function_calling"},
        )

    raise ValueError(f"unknown provider: {config.provider}")
