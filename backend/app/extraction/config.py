from dataclasses import dataclass

from pydantic import SecretStr

from app.core.errors import ConfigError
from app.core.settings import ExtractionSettings

PROVIDERS: tuple[str, ...] = ("google", "openai", "anthropic", "openrouter")

_KEY_FIELD_BY_PROVIDER = {
    "google": "google_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "openrouter": "openrouter_api_key",
}

_KEY_VARIABLE_BY_PROVIDER = {
    "google": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Filled in by step 22 (ADR 0006): provider -> default model used when LLM_MODEL is empty.
DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = {}


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    model: str
    api_key: SecretStr


@dataclass(frozen=True)
class TracingConfig:
    public_key: SecretStr
    secret_key: SecretStr
    host: str


def resolve_llm(
    settings: ExtractionSettings,
    provider: str | None = None,
    model: str | None = None,
) -> LlmConfig | None:
    effective_provider = provider if provider is not None else settings.llm_provider
    if not effective_provider:
        return None
    if effective_provider not in PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of: {', '.join(PROVIDERS)}")
    key_field = _KEY_FIELD_BY_PROVIDER[effective_provider]
    api_key = getattr(settings, key_field)
    if not api_key:
        variable = _KEY_VARIABLE_BY_PROVIDER[effective_provider]
        raise ConfigError(f"{variable} must be set for LLM_PROVIDER={effective_provider}")
    provider_overridden = provider is not None and provider != settings.llm_provider
    if model is not None:
        effective_model = model
    elif provider_overridden:
        effective_model = DEFAULT_MODEL_BY_PROVIDER.get(effective_provider, "")
        if not effective_model:
            raise ConfigError("--model must be given with --provider")
    else:
        effective_model = settings.llm_model or DEFAULT_MODEL_BY_PROVIDER.get(
            effective_provider, ""
        )
    if not effective_model:
        raise ConfigError("LLM_MODEL must be set")
    return LlmConfig(provider=effective_provider, model=effective_model, api_key=api_key)


def resolve_tracing(settings: ExtractionSettings) -> TracingConfig | None:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        return TracingConfig(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return None
