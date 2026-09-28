from dataclasses import dataclass

from app.core.errors import ConfigError
from app.core.settings import TweetSettings

SOURCE_REQUIREMENTS: dict[str, list[str]] = {
    "twscrape": ["TWSCRAPE_USERNAME", "TWSCRAPE_COOKIES"],
    "twitterapi_io": ["TWITTERAPI_IO_KEY"],
    "x_api": ["X_API_BEARER_TOKEN"],
}

_FIELD_BY_VARIABLE = {
    "TWSCRAPE_USERNAME": "twscrape_username",
    "TWSCRAPE_COOKIES": "twscrape_cookies",
    "TWITTERAPI_IO_KEY": "twitterapi_io_key",
    "X_API_BEARER_TOKEN": "x_api_bearer_token",
}


@dataclass(frozen=True)
class IngestConfig:
    source_name: str
    list_id: int
    settings: TweetSettings


def _cookie_names(cookies: str) -> set[str]:
    names = set()
    for part in cookies.split(";"):
        part = part.strip()
        if "=" in part:
            names.add(part.split("=", 1)[0].strip())
    return names


def check_source(settings: TweetSettings, name: str) -> None:
    for variable in SOURCE_REQUIREMENTS[name]:
        field = _FIELD_BY_VARIABLE[variable]
        value = getattr(settings, field)
        if not value:
            raise ConfigError(f"{variable} must be set for TWEET_SOURCE={name}")
    if name == "twscrape":
        names = _cookie_names(settings.twscrape_cookies.get_secret_value())
        if "auth_token" not in names or "ct0" not in names:
            raise ConfigError("TWSCRAPE_COOKIES must include auth_token and ct0")


def resolve_ingest(settings: TweetSettings) -> IngestConfig | None:
    if not settings.tweet_source:
        return None
    if settings.tweet_source not in SOURCE_REQUIREMENTS:
        raise ConfigError("TWEET_SOURCE must be one of: twscrape, twitterapi_io, x_api")
    check_source(settings, settings.tweet_source)
    if not settings.x_list_id or not settings.x_list_id.isdigit():
        raise ConfigError("X_LIST_ID must be set to the numeric ID of the watched X List")
    return IngestConfig(settings.tweet_source, int(settings.x_list_id), settings)
