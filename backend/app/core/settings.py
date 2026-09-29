import tempfile
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.errors import ConfigError

_POSTGRES_DRIVERNAMES = {"postgresql", "postgres", "postgresql+psycopg"}
_SCHEME_ERROR = ConfigError(
    "DATABASE_URL must be a PostgreSQL URL (postgresql://, postgres:// or postgresql+psycopg://)"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    fpl_league_ids: str = ""


class ExtractionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    llm_model: str = ""
    llm_fallback_model: str = ""
    openrouter_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    usd_pln_rate: float | None = None


class TweetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    tweet_source: str = ""
    x_list_id: str = ""
    twscrape_username: str = ""
    twscrape_cookies: SecretStr | None = None
    twscrape_accounts_db: str = Field(
        default_factory=lambda: str(Path(tempfile.gettempdir()) / "twscrape-accounts.db")
    )
    twitterapi_io_key: SecretStr | None = None
    x_api_bearer_token: SecretStr | None = None


def load_extraction_settings() -> ExtractionSettings:
    try:
        return ExtractionSettings()
    except ValidationError as exc:
        variables = sorted({str(error["loc"][0]).upper() for error in exc.errors() if error["loc"]})
        raise ConfigError(f"invalid value of {', '.join(variables)}") from None


def load_settings() -> Settings:
    try:
        settings = Settings()
    except ValidationError:
        raise ConfigError("DATABASE_URL must be set") from None
    settings.database_url = normalize_database_url(settings.database_url)
    return settings


def normalize_database_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise ConfigError("DATABASE_URL must be set")
    try:
        url = make_url(raw)
    except ArgumentError:
        raise _SCHEME_ERROR from None
    if url.drivername not in _POSTGRES_DRIVERNAMES:
        raise _SCHEME_ERROR
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def parse_league_ids(raw: str) -> list[int]:
    error = ConfigError("FPL_LEAGUE_IDS must be a comma-separated list of league IDs")
    if not raw.strip():
        raise error
    parts = [part.strip() for part in raw.split(",")]
    league_ids = []
    for part in parts:
        if not part.isdigit() or int(part) <= 0:
            raise error
        league_ids.append(int(part))
    return league_ids
