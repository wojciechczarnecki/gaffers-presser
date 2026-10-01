import pytest

from app.core.settings import TweetSettings


@pytest.fixture(autouse=True)
def _no_tweet_settings_from_the_environment(monkeypatch):
    for field in TweetSettings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
