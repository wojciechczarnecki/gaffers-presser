import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.extraction.config import LlmConfig
from app.extraction.providers import build_chat_model

DUMMY_KEY = SecretStr("dummy-key")


def test_google_provider_builds_offline():
    spec = build_chat_model(LlmConfig("google", "gemini-2.0-flash", DUMMY_KEY))
    assert isinstance(spec.chat_model, ChatGoogleGenerativeAI)
    assert spec.chat_model.model.endswith("gemini-2.0-flash")
    assert spec.chat_model.temperature == 0
    assert spec.chat_model.max_retries == 0
    assert spec.structured_kwargs == {}


def test_openai_provider_builds_offline():
    spec = build_chat_model(LlmConfig("openai", "gpt-4.1-mini", DUMMY_KEY))
    assert isinstance(spec.chat_model, ChatOpenAI)
    assert spec.chat_model.model_name == "gpt-4.1-mini"
    assert spec.chat_model.temperature == 0
    assert spec.chat_model.max_retries == 0
    assert spec.structured_kwargs == {}


def test_anthropic_provider_builds_offline():
    spec = build_chat_model(LlmConfig("anthropic", "claude-haiku-4", DUMMY_KEY))
    assert isinstance(spec.chat_model, ChatAnthropic)
    assert spec.chat_model.model == "claude-haiku-4"
    assert spec.chat_model.temperature == 0
    assert spec.chat_model.max_retries == 0
    assert spec.structured_kwargs == {}


def test_openrouter_provider_builds_offline():
    spec = build_chat_model(LlmConfig("openrouter", "openai/gpt-4.1-mini", DUMMY_KEY))
    assert isinstance(spec.chat_model, ChatOpenAI)
    assert spec.chat_model.model_name == "openai/gpt-4.1-mini"
    assert spec.chat_model.openai_api_base == "https://openrouter.ai/api/v1"
    assert spec.chat_model.temperature == 0
    assert spec.structured_kwargs == {"method": "function_calling"}


@pytest.mark.parametrize(
    "provider,model,expect_temperature",
    [
        ("openai", "gpt-5-nano", False),
        ("openrouter", "openai/o4-mini", False),
        ("openai", "gpt-4.1-mini", True),
        ("anthropic", "claude-haiku-4", True),
    ],
)
def test_reasoning_models_get_no_temperature(provider, model, expect_temperature):
    spec = build_chat_model(LlmConfig(provider, model, DUMMY_KEY))
    if expect_temperature:
        assert spec.chat_model.temperature == 0
    else:
        assert spec.chat_model.temperature is None
