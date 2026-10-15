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
    from app.core.retry import with_retries
    from app.llm.settings import LlmSettings
    from app.llm.tracing import TracingConfig, make_generation_tracer, resolve_tracing

    assert Clock and StopAwareClock and SystemClock and with_retries
    assert not (APP / "llm" / "retry.py").exists()
    assert TracingConfig and make_generation_tracer and resolve_tracing
    assert set(LlmSettings.model_fields) == {
        "openrouter_api_key",
        "langfuse_public_key",
        "langfuse_secret_key",
        "langfuse_host",
        "usd_pln_rate",
    }


# The edges each top-level module may import, along the data flow; worker/cli.py is the
# composition root and may import every module.
ALLOWED = {
    "content": set(),
    "core": set(),
    "db": set(),
    "llm": {"core"},
    "fpl": {"core", "db"},
    "delivery": {"core", "db", "fpl"},
    "tweets": {"core", "db", "fpl"},
    "extraction": {"content", "core", "db", "fpl", "llm", "tweets"},
    "retrieval": {"content", "core", "db", "extraction", "fpl", "llm", "tweets"},
    "corroboration": {"content", "core", "db", "extraction", "fpl", "llm", "retrieval", "tweets"},
    "alerts": {"core", "corroboration", "db", "delivery", "extraction", "fpl", "llm", "tweets"},
    "presser": {"content", "core", "db", "delivery", "fpl", "llm"},
    "worker": {"core", "db", "fpl"},
}
COMPOSITION_ROOT = APP / "worker" / "cli.py"


def _imported_modules(path: Path, node: ast.ImportFrom | ast.Import) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    module = node.module or ""
    if node.level > 0:
        package = list(path.relative_to(APP.parent).parent.parts)
        base = package[: len(package) - node.level + 1]
        module = ".".join([*base, module] if module else base)
    if module == "app":
        return [f"app.{alias.name}" for alias in node.names]
    return [module]


def _edges() -> dict[str, set[tuple[str, str]]]:
    edges: dict[str, set[tuple[str, str]]] = {}
    for package in sorted(p.name for p in APP.iterdir() if (p / "__init__.py").exists()):
        found = edges.setdefault(package, set())
        for path, node in _imports(package):
            if path == COMPOSITION_ROOT:
                continue
            for name in _imported_modules(path, node):
                parts = name.split(".")
                if parts[0] == "app" and len(parts) > 1 and parts[1] != package:
                    found.add((parts[1], f"{path.relative_to(APP)}:{node.lineno}"))
    return edges


def test_every_module_has_its_allowed_edges():
    assert set(_edges()) == set(ALLOWED)


def test_modules_import_only_allowed_edges():
    offenders = [
        f"{where} imports app.{target}"
        for package, found in _edges().items()
        for target, where in sorted(found)
        if target not in ALLOWED.get(package, set())
    ]
    assert offenders == []


def test_allowed_edges_have_no_cycle():
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(module: str, path: list[str]) -> None:
        assert module not in visiting, " -> ".join([*path, module])
        if module in done:
            return
        visiting.add(module)
        for target in sorted(ALLOWED[module]):
            visit(target, [*path, module])
        visiting.remove(module)
        done.add(module)

    for module in sorted(ALLOWED):
        visit(module, [])


def test_relative_and_package_imports_are_edges():
    path = APP / "alerts" / "loop.py"
    found = [
        _imported_modules(path, ast.parse(source).body[0])
        for source in (
            "from app import worker",
            "from ..worker.jobs import run_job",
            "from .. import worker",
            "from .schedule import alert_deadlines",
        )
    ]
    assert found == [["app.worker"], ["app.worker.jobs"], ["app.worker"], ["app.alerts.schedule"]]


def test_the_composition_root_imports_the_loops():
    targets = {
        name.split(".")[1]
        for path, node in _imports("worker")
        if path == COMPOSITION_ROOT
        for name in _imported_modules(path, node)
        if name.startswith("app.")
    }
    assert {"alerts", "extraction", "presser", "retrieval", "tweets"} <= targets
