from app.core.errors import ConfigError
from app.core.settings import TweetSettings
from app.tweets.config import check_source
from app.tweets.sources.base import TweetSource
from app.tweets.sources.twitterapi_io import TwitterApiIoSource
from app.tweets.sources.twscrape_source import TwscrapeSource
from app.tweets.sources.x_api import XApiSource

SOURCE_NAMES = ("twscrape", "twitterapi_io", "x_api")


def build_source(name: str, settings: TweetSettings) -> TweetSource:
    if name not in SOURCE_NAMES:
        raise ConfigError(f"TWEET_SOURCE must be one of: {', '.join(SOURCE_NAMES)}")
    check_source(settings, name)
    if name == "twitterapi_io":
        return TwitterApiIoSource(api_key=settings.twitterapi_io_key.get_secret_value())
    if name == "x_api":
        return XApiSource(bearer_token=settings.x_api_bearer_token.get_secret_value())
    return TwscrapeSource(
        username=settings.twscrape_username,
        cookies=settings.twscrape_cookies.get_secret_value(),
        accounts_db=settings.twscrape_accounts_db,
    )
