import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
FEATURES = ("extraction", "retrieval")
RETRY_CONSTANTS = {"MAX_ATTEMPTS", "RETRY_BACKOFF_SECONDS"}


def _trees(package: str):
    for path in sorted((APP / package).rglob("*.py")):
        yield path, ast.parse(path.read_text())


def test_no_chat_model_or_retry_loop_outside_app_llm():
    offenders = []
    for package in FEATURES:
        for path, tree in _trees(package):
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    names = {t.id for t in node.targets if isinstance(t, ast.Name)}
                    if names & RETRY_CONSTANTS:
                        offenders.append(f"{path.relative_to(APP)}:{node.lineno} constant")
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id in RETRY_CONSTANTS:
                        offenders.append(f"{path.relative_to(APP)}:{node.lineno} constant")
                if isinstance(node, ast.FunctionDef) and "retr" in node.name.lower():
                    if any(isinstance(n, ast.For | ast.While) for n in ast.walk(node)):
                        offenders.append(f"{path.relative_to(APP)}:{node.lineno} {node.name}")
    assert offenders == []


def test_app_llm_imports_no_feature_module():
    offenders = []
    for path, tree in _trees("llm"):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if name.startswith(("app.extraction", "app.retrieval", "app.corroboration")):
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []


def test_chat_model_factory_and_catalogue_live_in_app_llm():
    offenders = []
    for package in FEATURES:
        for path, tree in _trees(package):
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.Name) and node.id == "ChatOpenRouter":
                    names = ["ChatOpenRouter"]
                if any(
                    n.startswith("langchain_openrouter") or n == "ChatOpenRouter" for n in names
                ):
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []
    assert (APP / "llm" / "model_settings.toml").is_file()
    assert not (APP / "extraction" / "model_settings.toml").exists()


def test_current_extraction_sql_lives_once():
    pattern = re.compile(r"DISTINCT ON \(tweet_x_id\)")
    found = {
        str(path.relative_to(APP)): len(pattern.findall(path.read_text()))
        for path in sorted(APP.rglob("*.py"))
        if pattern.search(path.read_text())
    }
    assert found == {"extraction/store.py": 1}


def test_schedules_use_fpl_deadline_helpers():
    for relative in ("tweets/schedule.py", "worker/schedule.py"):
        tree = ast.parse((APP / relative).read_text())
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.fpl.deadlines" in imported, relative
        defined = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and ("deadline_after" in node.name or "at_or_before" in node.name)
        ]
        assert defined == [], relative
    store = ast.parse((APP / "tweets/store.py").read_text())
    assert "upcoming_deadlines" not in {
        node.name for node in ast.walk(store) if isinstance(node, ast.FunctionDef)
    }
