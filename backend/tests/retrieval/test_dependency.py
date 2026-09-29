import importlib.metadata
import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_retrieval_dependencies_pinned_version():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pins = {}
    for dependency in data["project"]["dependencies"]:
        match = re.match(r"^([A-Za-z0-9_-]+)==([^\s;]+)$", dependency)
        if match:
            pins[match.group(1)] = match.group(2)
    for package in ["pgvector", "openrouter"]:
        assert package in pins, f"{package} must be pinned with == in pyproject.toml"
        assert importlib.metadata.version(package) == pins[package]
