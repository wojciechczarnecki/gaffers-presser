from dataclasses import dataclass

from pydantic import SecretStr

from app.core.errors import ConfigError
from app.llm.pricing import Price, load_prices
from app.llm.settings import LlmSettings, load_llm_settings

DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"


class RetrievalSettings(LlmSettings):
    embedding_model: str = ""


def load_retrieval_settings() -> RetrievalSettings:
    return load_llm_settings(RetrievalSettings)


@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    api_key: SecretStr
    prices: dict[str, Price]


def resolve_embedding(
    settings: RetrievalSettings,
    model: str | None = None,
    prices: dict[str, Price] | None = None,
) -> EmbeddingConfig | None:
    if settings.openrouter_api_key is None:
        return None
    if prices is None:
        try:
            prices = load_prices()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigError(f"prices.toml cannot be loaded: {type(exc).__name__}") from None

    if model:
        chosen, variable = model, "--model"
    elif settings.embedding_model:
        chosen, variable = settings.embedding_model, "EMBEDDING_MODEL"
    else:
        chosen, variable = DEFAULT_EMBEDDING_MODEL, "DEFAULT_EMBEDDING_MODEL"
    price = prices.get(chosen)
    if price is None:
        raise ConfigError(f"{variable} names a model with no row in prices.toml")
    if price.output_per_million is not None:
        raise ConfigError(f"{variable} names a chat model, not an embedding model")
    return EmbeddingConfig(model=chosen, api_key=settings.openrouter_api_key, prices=prices)
