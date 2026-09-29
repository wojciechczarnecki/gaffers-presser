import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / "model_settings.toml"

REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
STRUCTURED_METHODS = ("function_calling", "json_schema")


@dataclass(frozen=True)
class ModelSettings:
    reasoning_effort: str
    temperature: bool
    structured_method: str
    checked: str


def load_model_settings(path: Path = DEFAULT_PATH) -> dict[str, ModelSettings]:
    data = tomllib.loads(Path(path).read_text())
    settings = {}
    for model, entry in data.items():
        effort = entry["reasoning_effort"]
        if effort not in REASONING_EFFORTS:
            raise ValueError(f"{model}: reasoning_effort must be one of {REASONING_EFFORTS}")
        method = entry.get("structured_method", "function_calling")
        if method not in STRUCTURED_METHODS:
            raise ValueError(f"{model}: structured_method must be one of {STRUCTURED_METHODS}")
        settings[model] = ModelSettings(
            reasoning_effort=effort,
            temperature=bool(entry["temperature"]),
            structured_method=method,
            checked=entry["checked"],
        )
    return settings


def pair_compatible(primary: str, fallback: str, catalogue: dict[str, ModelSettings]) -> bool:
    """Whether the fallback can answer the primary's request through the `models` list.

    The request carries the primary's structured-output method and reasoning effort, so the
    fallback must use the same ones (checked live for the candidates on 2026-09-29).
    """
    first, second = catalogue.get(primary), catalogue.get(fallback)
    if first is None or second is None:
        return False
    return (
        first.structured_method == second.structured_method
        and first.reasoning_effort == second.reasoning_effort
    )
