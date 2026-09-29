import pytest

from app.core.errors import ConfigError
from app.llm.settings import LlmSettings, load_llm_settings


def test_env_names_unchanged(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-sentinel")
    monkeypatch.setenv("LANGFUSE_HOST", "https://lf.example")
    monkeypatch.setenv("USD_PLN_RATE", "3.9")
    settings = load_llm_settings(LlmSettings)
    assert settings.openrouter_api_key.get_secret_value() == "sk-sentinel"
    assert settings.langfuse_host == "https://lf.example"
    assert settings.usd_pln_rate == 3.9


def test_malformed_rate_names_only_the_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USD_PLN_RATE", "not-a-number")
    with pytest.raises(ConfigError) as caught:
        load_llm_settings(LlmSettings)
    assert str(caught.value) == "invalid value of USD_PLN_RATE"
    assert "not-a-number" not in str(caught.value)
