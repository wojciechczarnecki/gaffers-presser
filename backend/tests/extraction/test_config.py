import pytest

from app.core.errors import ConfigError
from app.extraction.config import ExtractionSettings
from app.llm import chat as config_module
from app.llm.chat import resolve_llm
from app.llm.models import ModelSettings
from app.llm.tracing import resolve_tracing

SENTINEL = "sentinel-secret-value"

ROW = ModelSettings(
    reasoning_effort="none", temperature=True, structured_method="function_calling", checked="x"
)
CATALOGUE = {
    "a/primary": ROW,
    "b/other": ROW,
    "c/fallback": ROW,
    "d/json-schema": ModelSettings(
        reasoning_effort="none", temperature=True, structured_method="json_schema", checked="x"
    ),
    "e/reasoning-low": ModelSettings(
        reasoning_effort="low", temperature=True, structured_method="function_calling", checked="x"
    ),
}


REAL_DEFAULTS = (config_module.DEFAULT_MODEL, config_module.DEFAULT_FALLBACK_MODEL)


@pytest.fixture(autouse=True)
def _no_configured_defaults(monkeypatch):
    # These tests use a fake catalogue; the real defaults are checked in the last test.
    monkeypatch.setattr(config_module, "DEFAULT_MODEL", "")
    monkeypatch.setattr(config_module, "DEFAULT_FALLBACK_MODEL", "")


def _settings(monkeypatch, **env: str) -> ExtractionSettings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ExtractionSettings(_env_file=None)


def test_disabled_without_key_even_with_model(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODEL="a/primary")
    assert resolve_llm(settings, catalogue=CATALOGUE) is None


def test_enabled_with_key_and_model(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="a/primary")
    config = resolve_llm(settings, catalogue=CATALOGUE)
    assert config.model == "a/primary"
    assert config.api_key.get_secret_value() == SENTINEL
    assert config.settings == ROW


def test_default_model_when_llm_model_empty(monkeypatch):
    monkeypatch.setattr(config_module, "DEFAULT_MODEL", "b/other")
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL)
    assert resolve_llm(settings, catalogue=CATALOGUE).model == "b/other"


def test_llm_model_overrides_default(monkeypatch):
    monkeypatch.setattr(config_module, "DEFAULT_MODEL", "b/other")
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="a/primary")
    assert resolve_llm(settings, catalogue=CATALOGUE).model == "a/primary"


def test_cli_model_overrides_llm_model(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="a/primary")
    assert resolve_llm(settings, model="b/other", catalogue=CATALOGUE).model == "b/other"


@pytest.mark.parametrize(
    "env,override,variable",
    [
        ({"LLM_MODEL": "x/unknown-model"}, None, "LLM_MODEL"),
        ({}, "x/unknown-model", "--model"),
        ({"LLM_MODEL": "a/primary", "LLM_FALLBACK_MODEL": "x/unknown-model"}, None, "LLM_FALLBACK"),
    ],
)
def test_unknown_model_names_the_variable_not_the_key(monkeypatch, env, override, variable):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, **env)
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings, model=override, catalogue=CATALOGUE)
    assert variable in str(exc_info.value)
    assert "model_settings.toml" in str(exc_info.value)
    assert SENTINEL not in str(exc_info.value)


def test_fallback_from_variable_then_default_and_dropped_when_equal(monkeypatch):
    monkeypatch.setattr(config_module, "DEFAULT_FALLBACK_MODEL", "b/other")
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="a/primary")
    assert resolve_llm(settings, catalogue=CATALOGUE).fallback_model == "b/other"

    settings = _settings(monkeypatch, LLM_FALLBACK_MODEL="c/fallback")
    config = resolve_llm(settings, catalogue=CATALOGUE)
    assert config.fallback_model == "c/fallback"
    assert config.fallback_settings == ROW

    settings = _settings(monkeypatch, LLM_FALLBACK_MODEL="a/primary")
    assert resolve_llm(settings, catalogue=CATALOGUE).fallback_model is None


@pytest.mark.parametrize("fallback", ["d/json-schema", "e/reasoning-low"])
def test_incompatible_explicit_fallback_raises_naming_the_variable(monkeypatch, fallback):
    settings = _settings(
        monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="a/primary", LLM_FALLBACK_MODEL=fallback
    )
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings, catalogue=CATALOGUE)
    assert "LLM_FALLBACK_MODEL" in str(exc_info.value)
    assert SENTINEL not in str(exc_info.value)


@pytest.mark.parametrize(
    "env,override",
    [({"LLM_MODEL": "d/json-schema"}, None), ({}, "e/reasoning-low")],
    ids=["llm-model", "cli-model"],
)
def test_incompatible_default_fallback_is_dropped(monkeypatch, env, override):
    monkeypatch.setattr(config_module, "DEFAULT_MODEL", "a/primary")
    monkeypatch.setattr(config_module, "DEFAULT_FALLBACK_MODEL", "c/fallback")
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, **env)
    config = resolve_llm(settings, model=override, catalogue=CATALOGUE)
    assert config.model == (override or env["LLM_MODEL"])
    assert config.fallback_model is None
    assert config.fallback_settings is None


def test_no_fallback_when_use_fallback_false(monkeypatch):
    settings = _settings(
        monkeypatch,
        OPENROUTER_API_KEY=SENTINEL,
        LLM_MODEL="a/primary",
        LLM_FALLBACK_MODEL="c/fallback",
    )
    config = resolve_llm(settings, use_fallback=False, catalogue=CATALOGUE)
    assert config.fallback_model is None
    assert config.fallback_settings is None


def test_llm_provider_variable_ignored(monkeypatch):
    assert "llm_provider" not in ExtractionSettings.model_fields
    settings = _settings(
        monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="a/primary", LLM_PROVIDER="google"
    )
    config = resolve_llm(settings, catalogue=CATALOGUE)
    assert config.model == "a/primary"
    assert not hasattr(settings, "llm_provider")


def test_errors_never_carry_key_values(monkeypatch):
    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="x/unknown-model")
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings, catalogue=CATALOGUE)
    assert SENTINEL not in str(exc_info.value)

    settings = _settings(monkeypatch, OPENROUTER_API_KEY=SENTINEL)
    monkeypatch.setattr(config_module, "DEFAULT_MODEL", "")
    with pytest.raises(ConfigError) as exc_info:
        resolve_llm(settings, catalogue=CATALOGUE)
    assert SENTINEL not in str(exc_info.value)


def test_real_catalogue_is_loaded_when_none_is_given(monkeypatch):
    settings = _settings(
        monkeypatch, OPENROUTER_API_KEY=SENTINEL, LLM_MODEL="google/gemini-3.1-flash-lite"
    )
    assert resolve_llm(settings).model == "google/gemini-3.1-flash-lite"


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


def test_default_models_are_catalogue_models():
    from app.llm.models import load_model_settings, pair_compatible

    catalogue = load_model_settings()
    default, fallback = REAL_DEFAULTS
    assert default in catalogue
    assert fallback in catalogue
    assert pair_compatible(default, fallback, catalogue)
