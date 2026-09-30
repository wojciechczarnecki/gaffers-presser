import pytest

from app.core.errors import ConfigError
from app.llm.pricing import Price
from app.retrieval.config import (
    DEFAULT_EMBEDDING_MODEL,
    RetrievalSettings,
    resolve_embedding,
)

SENTINEL_KEY = "sk-sentinel-key"
PRICES = {
    DEFAULT_EMBEDDING_MODEL: Price(input_per_million=0.02, output_per_million=None, checked="x"),
    "other/embed": Price(input_per_million=0.1, output_per_million=None, checked="x"),
    "chat/model": Price(input_per_million=0.1, output_per_million=0.5, checked="x"),
}


def _settings(monkeypatch, **env: str) -> RetrievalSettings:
    for name in ("OPENROUTER_API_KEY", "EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return RetrievalSettings(_env_file=None)


def test_default_model_name():
    assert DEFAULT_EMBEDDING_MODEL == "openai/text-embedding-3-small"


@pytest.mark.parametrize("env", [{}, {"EMBEDDING_MODEL": ""}])
def test_empty_or_unset_uses_default(monkeypatch, env):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL_KEY, **env)
    config = resolve_embedding(settings, prices=PRICES)
    assert config is not None
    assert config.model == DEFAULT_EMBEDDING_MODEL
    assert config.api_key.get_secret_value() == SENTINEL_KEY


def test_configured_model_is_used(monkeypatch):
    settings = _settings(
        monkeypatch, OPENROUTER_API_KEY=SENTINEL_KEY, EMBEDDING_MODEL="other/embed"
    )
    assert resolve_embedding(settings, prices=PRICES).model == "other/embed"


def test_unpriced_embedding_model_names_the_variable(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL_KEY, EMBEDDING_MODEL="x/unknown")
    with pytest.raises(ConfigError) as caught:
        resolve_embedding(settings, prices=PRICES)
    assert "EMBEDDING_MODEL" in str(caught.value)
    assert SENTINEL_KEY not in str(caught.value)


def test_chat_model_as_embedding_model_names_the_variable(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL_KEY, EMBEDDING_MODEL="chat/model")
    with pytest.raises(ConfigError, match="EMBEDDING_MODEL names a chat model"):
        resolve_embedding(settings, prices=PRICES)
    with pytest.raises(ConfigError, match="--model names a chat model"):
        resolve_embedding(settings, "chat/model", PRICES)


def test_model_option_overrides(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL_KEY)
    with pytest.raises(ConfigError, match="--model"):
        resolve_embedding(settings, "x/unknown", PRICES)
    assert resolve_embedding(settings, "other/embed", PRICES).model == "other/embed"


def test_no_key_disables(monkeypatch):
    settings = _settings(monkeypatch, EMBEDDING_MODEL="x/unknown")
    assert resolve_embedding(settings, prices=PRICES) is None


def test_unloadable_prices_named_without_key(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL_KEY)

    def broken() -> dict:
        raise OSError("boom")

    monkeypatch.setattr("app.retrieval.config.load_prices", broken)
    with pytest.raises(ConfigError, match="prices.toml cannot be loaded: OSError"):
        resolve_embedding(settings)
