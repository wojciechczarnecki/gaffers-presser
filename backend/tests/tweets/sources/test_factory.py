import asyncio
import tempfile

import pytest
from twscrape import API

from app.core.errors import ConfigError
from app.core.settings import TweetSettings
from app.tweets.config import resolve_ingest
from app.tweets.sources import SOURCE_NAMES, build_source
from app.tweets.sources.twitterapi_io import TwitterApiIoSource
from app.tweets.sources.twscrape_source import TwscrapeSource
from app.tweets.sources.x_api import XApiSource

SENTINEL = "sentinel-secret-value"

_EXPECTED_CLASS = {
    "twscrape": TwscrapeSource,
    "twitterapi_io": TwitterApiIoSource,
    "x_api": XApiSource,
}


def _settings(monkeypatch, **env: str) -> TweetSettings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TweetSettings(_env_file=None)


@pytest.mark.parametrize("name", SOURCE_NAMES)
def test_switching_source_needs_only_the_environment(monkeypatch, tmp_path, name):
    env = {"TWEET_SOURCE": name, "X_LIST_ID": "123"}
    if name == "twscrape":
        env["TWSCRAPE_USERNAME"] = "dedicated"
        env["TWSCRAPE_COOKIES"] = f"auth_token={SENTINEL}; ct0={SENTINEL}"
        env["TWSCRAPE_ACCOUNTS_DB"] = str(tmp_path / "accounts.db")
    elif name == "twitterapi_io":
        env["TWITTERAPI_IO_KEY"] = SENTINEL
    elif name == "x_api":
        env["X_API_BEARER_TOKEN"] = SENTINEL

    settings = _settings(monkeypatch, **env)
    config = resolve_ingest(settings)
    assert config.source_name == name

    source = build_source(config.source_name, config.settings)
    try:
        assert isinstance(source, _EXPECTED_CLASS[name])
    finally:
        source.close()


@pytest.mark.parametrize(
    "name,missing_variable",
    [
        ("twscrape", "TWSCRAPE_USERNAME"),
        ("twitterapi_io", "TWITTERAPI_IO_KEY"),
        ("x_api", "X_API_BEARER_TOKEN"),
    ],
)
def test_missing_credentials_names_the_variable(monkeypatch, name, missing_variable):
    settings = _settings(monkeypatch)
    with pytest.raises(ConfigError, match=missing_variable):
        build_source(name, settings)


def test_empty_accounts_db_falls_back_to_a_working_default(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    settings = _settings(
        monkeypatch,
        TWSCRAPE_USERNAME="dedicated",
        TWSCRAPE_COOKIES=f"auth_token={SENTINEL}; ct0={SENTINEL}",
        TWSCRAPE_ACCOUNTS_DB="",
    )
    assert settings.twscrape_accounts_db == str(tmp_path / "twscrape-accounts.db")

    source = build_source("twscrape", settings)
    source.close()

    accounts = asyncio.run(API(pool=settings.twscrape_accounts_db).pool.get_all())
    assert [account.username for account in accounts] == ["dedicated"]
