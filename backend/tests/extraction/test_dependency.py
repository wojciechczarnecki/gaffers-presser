import importlib.metadata
import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

_PACKAGES = [
    "langgraph",
    "langchain-core",
    "langchain-google-genai",
    "langchain-openai",
    "langchain-anthropic",
    "langfuse",
    "langchain",
    "langchain-openrouter",
]


def _pinned_versions() -> dict[str, str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pins = {}
    for dependency in data["project"]["dependencies"]:
        match = re.match(r"^([A-Za-z0-9_-]+)==([^\s;]+)$", dependency)
        if match:
            pins[match.group(1)] = match.group(2)
    return pins


def test_extraction_dependencies_pinned_version():
    pins = _pinned_versions()
    for package in _PACKAGES:
        assert package in pins, f"{package} must be pinned with == in pyproject.toml"
        assert importlib.metadata.version(package) == pins[package]
