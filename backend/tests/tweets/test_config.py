import pytest

from app.core.errors import ConfigError
from app.core.settings import TweetSettings
from app.tweets.config import check_source, resolve_ingest

SENTINEL = "sentinel-secret-value"


def _settings(monkeypatch, **env: str) -> TweetSettings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TweetSettings(_env_file=None)


def test_empty_source_disables_ingest(monkeypatch):
    settings = _settings(monkeypatch)
    assert resolve_ingest(settings) is None


def test_unknown_source_name_does_not_echo_value(monkeypatch):
    settings = _settings(monkeypatch, TWEET_SOURCE="bogus-source")
    with pytest.raises(ConfigError) as exc_info:
        resolve_ingest(settings)
    assert "TWEET_SOURCE" in str(exc_info.value)
    assert "bogus-source" not in str(exc_info.value)


@pytest.mark.parametrize(
    "name,missing_variable",
    [
        ("twscrape", "TWSCRAPE_USERNAME"),
        ("twitterapi_io", "TWITTERAPI_IO_KEY"),
        ("x_api", "X_API_BEARER_TOKEN"),
    ],
)
def test_missing_credential_names_the_variable(monkeypatch, name, missing_variable):
    settings = _settings(monkeypatch, TWEET_SOURCE=name, X_LIST_ID="123")
    with pytest.raises(ConfigError, match=missing_variable):
        resolve_ingest(settings)


@pytest.mark.parametrize("list_id", ["", "not-a-number"])
def test_missing_or_non_numeric_list_id(monkeypatch, list_id):
    settings = _settings(
        monkeypatch,
        TWEET_SOURCE="twitterapi_io",
        TWITTERAPI_IO_KEY=SENTINEL,
        X_LIST_ID=list_id,
    )
    with pytest.raises(ConfigError, match="X_LIST_ID"):
        resolve_ingest(settings)


def test_twscrape_cookies_must_include_auth_token_and_ct0(monkeypatch):
    settings = _settings(
        monkeypatch,
        TWEET_SOURCE="twscrape",
        X_LIST_ID="123",
        TWSCRAPE_USERNAME="dedicated",
        TWSCRAPE_COOKIES=f"ct0={SENTINEL}",
    )
    with pytest.raises(ConfigError, match="TWSCRAPE_COOKIES") as exc_info:
        resolve_ingest(settings)
    assert "sentinel-secret" not in str(exc_info.value)


def test_twscrape_cookies_with_both_names_pass(monkeypatch):
    settings = _settings(
        monkeypatch,
        TWEET_SOURCE="twscrape",
        X_LIST_ID="123",
        TWSCRAPE_USERNAME="dedicated",
        TWSCRAPE_COOKIES=f"auth_token={SENTINEL}; ct0={SENTINEL}",
    )
    config = resolve_ingest(settings)
    assert config.source_name == "twscrape"
    assert config.list_id == 123


def test_secret_values_never_appear_in_error_or_repr(monkeypatch):
    settings = _settings(
        monkeypatch,
        TWEET_SOURCE="x_api",
        X_LIST_ID="123",
        X_API_BEARER_TOKEN=SENTINEL,
    )
    resolve_ingest(settings)
    with pytest.raises(ConfigError) as exc_info:
        check_source(settings, "twscrape")
    assert SENTINEL not in str(exc_info.value)
    assert SENTINEL not in repr(settings)
