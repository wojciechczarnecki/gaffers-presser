import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
CLOCK_NAMES = {"Clock", "SystemClock", "StopAwareClock"}


def _imports(package: str) -> list[tuple[Path, ast.ImportFrom | ast.Import]]:
    found = []
    for path in sorted((APP / package).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom | ast.Import):
                found.append((path, node))
    return found


def test_extraction_takes_no_clock_from_tweets_or_worker():
    imports = _imports("extraction")
    assert imports
    offenders = [
        f"{path.relative_to(APP)}:{node.lineno}"
        for path, node in imports
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith(("app.tweets", "app.worker"))
        and CLOCK_NAMES & {alias.name for alias in node.names}
    ]
    assert offenders == []


def test_shared_layer_lives_in_app_llm_and_core():
    from app.core.clock import Clock, StopAwareClock, SystemClock

    assert Clock and StopAwareClock and SystemClock


def test_retrieval_imports_nothing_from_extraction():
    files = list((APP / "retrieval").rglob("*.py"))
    assert files
    offenders = []
    for path, node in _imports("retrieval"):
        names = (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [alias.name for alias in node.names]
        )
        if any(name == "app.extraction" or name.startswith("app.extraction.") for name in names):
            offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []
