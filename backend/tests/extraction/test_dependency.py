import importlib.metadata
import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PYPROJECT = BACKEND / "pyproject.toml"
UV_LOCK = BACKEND / "uv.lock"

_REMOVED = ["langchain-google-genai", "langchain-openai", "langchain-anthropic"]
_REMOVED_IMPORT = re.compile(r"^\s*(from|import)\s+langchain_(openai|google_genai|anthropic)\b")

_PACKAGES = [
    "langgraph",
    "langchain-core",
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


def test_removed_llm_packages_absent():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = " ".join(data["project"]["dependencies"])
    lock = UV_LOCK.read_text(encoding="utf-8")
    for package in _REMOVED:
        assert package not in declared
        assert f'name = "{package}"' not in lock


def test_no_module_imports_removed_packages():
    offenders = []
    for path in [*(BACKEND / "app").rglob("*.py"), *(BACKEND / "tests").rglob("*.py")]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _REMOVED_IMPORT.match(line):
                offenders.append(f"{path.relative_to(BACKEND)}:{number}")
    assert offenders == []
