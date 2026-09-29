from dataclasses import dataclass

from pydantic import SecretStr

from app.core.errors import ConfigError
from app.extraction.model_settings import ModelSettings, load_model_settings, pair_compatible
from app.llm.settings import LlmSettings, load_llm_settings

PROVIDER = "openrouter"


class ExtractionSettings(LlmSettings):
    llm_model: str = ""
    llm_fallback_model: str = ""


def load_extraction_settings() -> ExtractionSettings:
    return load_llm_settings(ExtractionSettings)


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
