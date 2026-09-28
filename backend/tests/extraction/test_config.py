import pytest

from app.core.errors import ConfigError
from app.core.settings import ExtractionSettings
from app.extraction.config import (
    DEFAULT_MODEL_BY_PROVIDER,
    PROVIDERS,
    resolve_llm,
    resolve_tracing,
)

SENTINEL = "sentinel-secret-value"

_KEY_VARIABLE_BY_PROVIDER = {
    "google": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _settings(monkeypatch, **env: str) -> ExtractionSettings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ExtractionSettings(_env_file=None)


def test_extraction_disabled_when_provider_empty(monkeypatch):
    settings = _settings(monkeypatch)
    assert resolve_llm(settings) is None


@pytest.mark.parametrize("provider", PROVIDERS)
def test_each_provider_resolves_with_its_key(monkeypatch, provider):
    variable = _KEY_VARIABLE_BY_PROVIDER[provider]
    settings = _settings(
        monkeypatch,
        LLM_PROVIDER=provider,
        LLM_MODEL="a-model",
        **{variable: SENTINEL},
    )
    config = resolve_llm(settings)
    assert config.provider == provider
    assert config.model == "a-model"
    assert config.api_key.get_secret_value() == SENTINEL


def test_unknown_provider_does_not_echo_value(monkeypatch):
    settings = _settings(monkeypatch, LLM_PROVIDER="bogus-provider")
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings)
    assert "LLM_PROVIDER" in str(exc_info.value)
    assert "bogus-provider" not in str(exc_info.value)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_missing_key_names_the_variable(monkeypatch, provider):
    variable = _KEY_VARIABLE_BY_PROVIDER[provider]
    settings = _settings(monkeypatch, LLM_PROVIDER=provider, LLM_MODEL="a-model")
    with pytest.raises(ConfigError, match=variable):
        resolve_llm(settings)


def test_empty_model_with_no_default_raises(monkeypatch):
    settings = _settings(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY=SENTINEL)
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        resolve_llm(settings)


def test_overrides_take_precedence_over_settings(monkeypatch):
    settings = _settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="settings-model",
        OPENAI_API_KEY=SENTINEL,
        ANTHROPIC_API_KEY=SENTINEL,
    )
    config = resolve_llm(settings, provider="anthropic", model="override-model")
    assert config.provider == "anthropic"
    assert config.model == "override-model"


def test_other_provider_without_model_does_not_borrow_the_settings_model(monkeypatch):
    settings = _settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="gpt-4.1-mini",
        OPENAI_API_KEY=SENTINEL,
        ANTHROPIC_API_KEY=SENTINEL,
    )
    with pytest.raises(ConfigError, match="--model must be given with --provider"):
        resolve_llm(settings, provider="anthropic")


def test_other_provider_without_model_takes_the_provider_default(monkeypatch):
    monkeypatch.setitem(DEFAULT_MODEL_BY_PROVIDER, "anthropic", "anthropic-default")
    settings = _settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="gpt-4.1-mini",
        OPENAI_API_KEY=SENTINEL,
        ANTHROPIC_API_KEY=SENTINEL,
    )
    config = resolve_llm(settings, provider="anthropic")
    assert (config.provider, config.model) == ("anthropic", "anthropic-default")


def test_same_provider_override_keeps_the_settings_model(monkeypatch):
    settings = _settings(
        monkeypatch, LLM_PROVIDER="openai", LLM_MODEL="gpt-4.1-mini", OPENAI_API_KEY=SENTINEL
    )
    config = resolve_llm(settings, provider="openai")
    assert config.model == "gpt-4.1-mini"


def test_errors_never_carry_values(monkeypatch):
    settings = _settings(
        monkeypatch,
        LLM_PROVIDER="bogus-provider",
        OPENAI_API_KEY=SENTINEL,
        GOOGLE_API_KEY=SENTINEL,
    )
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings)
    assert SENTINEL not in str(exc_info.value)

    settings = _settings(monkeypatch, LLM_PROVIDER="openai")
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings)
    assert SENTINEL not in str(exc_info.value)


def test_resolve_tracing_with_both_keys(monkeypatch):
    settings = _settings(
        monkeypatch,
        LANGFUSE_PUBLIC_KEY=SENTINEL,
        LANGFUSE_SECRET_KEY=SENTINEL,
    )
    tracing = resolve_tracing(settings)
    assert tracing is not None
    assert tracing.public_key.get_secret_value() == SENTINEL
    assert tracing.host == "https://cloud.langfuse.com"


@pytest.mark.parametrize(
    "env",
    [
        {"LANGFUSE_PUBLIC_KEY": SENTINEL},
        {"LANGFUSE_SECRET_KEY": SENTINEL},
        {},
    ],
)
def test_resolve_tracing_with_one_or_no_keys(monkeypatch, env):
    settings = _settings(monkeypatch, **env)
    assert resolve_tracing(settings) is None
