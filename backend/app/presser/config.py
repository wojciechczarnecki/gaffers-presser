import json

from app.core.errors import ConfigError
from app.llm.chat import LlmConfig, single_model_config
from app.llm.settings import LlmSettings

DEFAULT_PRESSER_MODEL = "openai/gpt-6-luna"
MAX_NICKNAME_LENGTH = 30

_SHAPE = "PRESSER_NICKNAMES must be a JSON object of FPL entry ID to nickname"


class PresserSettings(LlmSettings):
    presser_enabled: str = "true"
    presser_model: str = DEFAULT_PRESSER_MODEL
    # a str, so that pydantic never echoes the value in a validation error
    presser_nicknames: str = ""


def parse_nicknames(settings: PresserSettings) -> dict[int, str]:
    raw = settings.presser_nicknames.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise ConfigError(_SHAPE) from None
    if not isinstance(data, dict):
        raise ConfigError(_SHAPE)
    limits = f"{_SHAPE}, with non-empty nicknames of at most {MAX_NICKNAME_LENGTH} characters"
    nicknames: dict[int, str] = {}
    for key, nickname in data.items():
        try:
            entry_id = int(key)
        except ValueError:
            raise ConfigError(limits) from None
        if (
            entry_id < 1
            or not isinstance(nickname, str)
            or not nickname.strip()
            or len(nickname) > MAX_NICKNAME_LENGTH
        ):
            raise ConfigError(limits)
        nicknames[entry_id] = nickname.strip()
    folded = [nickname.casefold() for nickname in nicknames.values()]
    if len(set(folded)) != len(folded):
        raise ConfigError("PRESSER_NICKNAMES has a duplicate nickname")
    return nicknames


def _enabled(settings: PresserSettings) -> bool:
    value = settings.presser_enabled.strip().lower() or "true"
    if value not in ("true", "false"):
        raise ConfigError("PRESSER_ENABLED must be true or false")
    return value == "true"


def presser_disabled_reason(settings: PresserSettings, *, delivery: bool) -> str | None:
    if not _enabled(settings):
        return "PRESSER_ENABLED=false"
    if settings.openrouter_api_key is None:
        return "OPENROUTER_API_KEY is not set"
    if not delivery:
        return "delivery disabled"
    return None


def resolve_presser_llm(settings: PresserSettings) -> LlmConfig | None:
    if settings.openrouter_api_key is None:
        return None
    return single_model_config(
        settings.openrouter_api_key, settings.presser_model, variable="PRESSER_MODEL"
    )
