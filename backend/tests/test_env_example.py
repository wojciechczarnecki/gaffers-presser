from pathlib import Path

from app.alerts.config import AlertSettings
from app.core.settings import TweetSettings
from app.delivery.config import DeliverySettings
from app.extraction.config import ExtractionSettings
from app.retrieval.config import RetrievalSettings

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / "backend" / ".env.example"

_FIELD_TO_VARIABLE = {
    "tweet_source": "TWEET_SOURCE",
    "x_list_id": "X_LIST_ID",
    "twscrape_username": "TWSCRAPE_USERNAME",
    "twscrape_cookies": "TWSCRAPE_COOKIES",
    "twscrape_accounts_db": "TWSCRAPE_ACCOUNTS_DB",
    "twitterapi_io_key": "TWITTERAPI_IO_KEY",
    "x_api_bearer_token": "X_API_BEARER_TOKEN",
}

_EXTRACTION_FIELD_TO_VARIABLE = {
    "llm_model": "LLM_MODEL",
    "llm_fallback_model": "LLM_FALLBACK_MODEL",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "langfuse_public_key": "LANGFUSE_PUBLIC_KEY",
    "langfuse_secret_key": "LANGFUSE_SECRET_KEY",
    "langfuse_host": "LANGFUSE_HOST",
    "usd_pln_rate": "USD_PLN_RATE",
}

_RETRIEVAL_FIELD_TO_VARIABLE = {
    "embedding_model": "EMBEDDING_MODEL",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "langfuse_public_key": "LANGFUSE_PUBLIC_KEY",
    "langfuse_secret_key": "LANGFUSE_SECRET_KEY",
    "langfuse_host": "LANGFUSE_HOST",
    "usd_pln_rate": "USD_PLN_RATE",
}


_DELIVERY_FIELD_TO_VARIABLE = {
    "delivery_provider": "DELIVERY_PROVIDER",
    "resend_api_key": "RESEND_API_KEY",
    "delivery_email_to": "DELIVERY_EMAIL_TO",
    "delivery_email_from": "DELIVERY_EMAIL_FROM",
}


_ALERT_FIELD_TO_VARIABLE = {
    "alerts_enabled": "ALERTS_ENABLED",
    "alert_slots_minutes": "ALERT_SLOTS_MINUTES",
    "alert_trending_min_accounts": "ALERT_TRENDING_MIN_ACCOUNTS",
    "alert_widely_owned_percent": "ALERT_WIDELY_OWNED_PERCENT",
    "alert_rehearsal_deadline": "ALERT_REHEARSAL_DEADLINE",
}


def _lines() -> list[str]:
    return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def test_every_tweet_setting_field_has_its_variable_covered():
    assert set(TweetSettings.model_fields) == set(_FIELD_TO_VARIABLE)


def test_every_tweet_setting_is_listed_in_env_example():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for variable in _FIELD_TO_VARIABLE.values():
        assert variable in text, f"{variable!r} missing from backend/.env.example"


def test_every_tweet_variable_is_an_empty_placeholder():
    tweet_variables = set(_FIELD_TO_VARIABLE.values())
    seen = set()
    for line in _lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name in tweet_variables:
            seen.add(name)
            assert value == "", f"{name} must be an empty placeholder in .env.example"
    assert seen == tweet_variables


def test_every_extraction_setting_field_has_its_variable_covered():
    assert set(ExtractionSettings.model_fields) == set(_EXTRACTION_FIELD_TO_VARIABLE)


def test_every_extraction_setting_is_listed_in_env_example():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for variable in _EXTRACTION_FIELD_TO_VARIABLE.values():
        assert variable in text, f"{variable!r} missing from backend/.env.example"


def test_every_extraction_variable_is_an_empty_placeholder():
    extraction_variables = set(_EXTRACTION_FIELD_TO_VARIABLE.values())
    seen = set()
    for line in _lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name in extraction_variables:
            seen.add(name)
            assert value == "", f"{name} must be an empty placeholder in .env.example"
    assert seen == extraction_variables


def test_every_retrieval_setting_field_has_its_variable_covered():
    assert set(RetrievalSettings.model_fields) == set(_RETRIEVAL_FIELD_TO_VARIABLE)


def test_every_retrieval_setting_is_an_empty_placeholder():
    retrieval_variables = set(_RETRIEVAL_FIELD_TO_VARIABLE.values())
    seen = set()
    for line in _lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name in retrieval_variables:
            seen.add(name)
            assert value == "", f"{name} must be an empty placeholder in .env.example"
    assert seen == retrieval_variables


def test_every_delivery_setting_field_has_its_variable_covered():
    assert set(DeliverySettings.model_fields) == set(_DELIVERY_FIELD_TO_VARIABLE)


def test_every_delivery_variable_is_an_empty_placeholder():
    delivery_variables = set(_DELIVERY_FIELD_TO_VARIABLE.values())
    seen = set()
    for line in _lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name in delivery_variables:
            seen.add(name)
            assert value == "", f"{name} must be an empty placeholder in .env.example"
    assert seen == delivery_variables


def test_every_alert_setting_field_has_its_variable_covered():
    assert set(AlertSettings.model_fields) == set(_ALERT_FIELD_TO_VARIABLE)


def test_every_alert_variable_is_an_empty_placeholder():
    alert_variables = set(_ALERT_FIELD_TO_VARIABLE.values())
    seen = set()
    for line in _lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name in alert_variables:
            seen.add(name)
            assert value == "", f"{name} must be an empty placeholder in .env.example"
    assert seen == alert_variables
