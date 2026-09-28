from pathlib import Path

from app.core.settings import TweetSettings

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
