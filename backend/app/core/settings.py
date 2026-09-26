from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    fpl_league_ids: str = ""


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
