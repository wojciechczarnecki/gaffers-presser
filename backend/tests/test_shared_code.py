import ast
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
