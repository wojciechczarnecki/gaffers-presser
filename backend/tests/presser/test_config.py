import json
import os
import re

import pytest

from app.core.errors import ConfigError
from app.llm.chat import LlmConfig
from app.presser.config import (
    DEFAULT_PRESSER_MODEL,
    PresserSettings,
    parse_nicknames,
    presser_disabled_reason,
    resolve_presser_llm,
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in list(os.environ):
        if re.match(r"PRESSER|OPENROUTER", name):
            monkeypatch.delenv(name)


def settings(**values) -> PresserSettings:
    return PresserSettings(_env_file=None, **values)


def test_default_model():
    assert DEFAULT_PRESSER_MODEL == "openai/gpt-6-luna"
    assert settings().presser_model == "openai/gpt-6-luna"


def test_default_model_resolves_with_a_key():
    config = resolve_presser_llm(settings(openrouter_api_key="k"))
    assert isinstance(config, LlmConfig)
    assert config.model == "openai/gpt-6-luna"
    assert config.fallback_model is None


def test_resolve_without_key_is_none():
    assert resolve_presser_llm(settings()) is None


def test_unknown_model_names_the_variable():
    with pytest.raises(ConfigError, match="PRESSER_MODEL"):
        resolve_presser_llm(settings(openrouter_api_key="k", presser_model="no/such-model"))


def test_valid_nicknames_parsed():
    assert parse_nicknames(settings(presser_nicknames='{"123": "Bartas", "7": "Kuba"}')) == {
        123: "Bartas",
        7: "Kuba",
    }


def test_empty_nicknames_are_empty():
    assert parse_nicknames(settings()) == {}


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        json.dumps(["Bartas"]),
        json.dumps({"abc": "Bartas"}),
        json.dumps({"0": "Bartas"}),
        json.dumps({"1": ""}),
        json.dumps({"1": "x" * 31}),
        json.dumps({"1": "Bartas", "2": "bartas"}),
        json.dumps({"1": 5}),
    ],
)
def test_invalid_nicknames_error_names_variable_not_value(value):
    with pytest.raises(ConfigError) as error:
        parse_nicknames(settings(presser_nicknames=value))
    message = str(error.value)
    assert "PRESSER_NICKNAMES" in message
    assert value not in message
    assert "Bartas" not in message
    assert "not json" not in message


def test_disabled_reasons():
    assert presser_disabled_reason(settings(presser_enabled="false"), delivery=True) == (
        "PRESSER_ENABLED=false"
    )
    assert presser_disabled_reason(settings(), delivery=True) == "OPENROUTER_API_KEY is not set"
    assert (
        presser_disabled_reason(settings(openrouter_api_key="k"), delivery=False)
        == "delivery disabled"
    )
    assert presser_disabled_reason(settings(openrouter_api_key="k"), delivery=True) is None


def test_invalid_enabled_value_is_a_config_error():
    with pytest.raises(ConfigError, match="PRESSER_ENABLED"):
        presser_disabled_reason(settings(presser_enabled="maybe"), delivery=True)
