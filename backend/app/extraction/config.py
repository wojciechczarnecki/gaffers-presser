from dataclasses import dataclass

from pydantic import SecretStr

from app.core.errors import ConfigError
from app.core.settings import ExtractionSettings
from app.extraction.model_settings import ModelSettings, load_model_settings

PROVIDER = "openrouter"

# Set from ADR 0006 once the comparison has run.
DEFAULT_MODEL = ""
DEFAULT_FALLBACK_MODEL = ""


@dataclass(frozen=True)
class LlmConfig:
    model: str
    fallback_model: str | None
    api_key: SecretStr
    settings: ModelSettings
    fallback_settings: ModelSettings | None


@dataclass(frozen=True)
class TracingConfig:
    public_key: SecretStr
    secret_key: SecretStr
    host: str


def _catalogue_row(catalogue: dict[str, ModelSettings], model: str, variable: str) -> ModelSettings:
    row = catalogue.get(model)
    if row is None:
        raise ConfigError(f"{variable} names a model with no entry in model_settings.toml")
    return row


def resolve_llm(
    settings: ExtractionSettings,
    model: str | None = None,
    use_fallback: bool = True,
    catalogue: dict[str, ModelSettings] | None = None,
) -> LlmConfig | None:
    if settings.openrouter_api_key is None:
        return None
    if catalogue is None:
        try:
            catalogue = load_model_settings()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigError(
                f"model_settings.toml cannot be loaded: {type(exc).__name__}"
            ) from None

    if model:
        primary, primary_variable = model, "--model"
    elif settings.llm_model:
        primary, primary_variable = settings.llm_model, "LLM_MODEL"
    else:
        primary, primary_variable = DEFAULT_MODEL, "DEFAULT_MODEL"
    if not primary:
        raise ConfigError("LLM_MODEL must be set")
    primary_row = _catalogue_row(catalogue, primary, primary_variable)

    fallback: str | None = None
    fallback_row: ModelSettings | None = None
    if use_fallback:
        if settings.llm_fallback_model:
            candidate, fallback_variable = settings.llm_fallback_model, "LLM_FALLBACK_MODEL"
        else:
            candidate, fallback_variable = DEFAULT_FALLBACK_MODEL, "DEFAULT_FALLBACK_MODEL"
        if candidate and candidate != primary:
            fallback = candidate
            fallback_row = _catalogue_row(catalogue, candidate, fallback_variable)

    return LlmConfig(
        model=primary,
        fallback_model=fallback,
        api_key=settings.openrouter_api_key,
        settings=primary_row,
        fallback_settings=fallback_row,
    )


def resolve_tracing(settings: ExtractionSettings) -> TracingConfig | None:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        return TracingConfig(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return None
