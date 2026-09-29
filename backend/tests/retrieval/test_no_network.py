import ast
from pathlib import Path

from app.retrieval.embedder import Embedder
from tests.retrieval.fakes import FakeEmbedder

TESTS = Path(__file__).resolve().parent


def test_openrouter_embedder_unused_without_injected_client():
    embedder: Embedder = FakeEmbedder()
    assert embedder.model == "fake/embed"
    offenders = []
    for path in sorted((TESTS).rglob("*.py")):
        if path.name == "test_no_network.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "OpenRouterEmbedder"
                and not any(keyword.arg == "client" for keyword in node.keywords)
            ):
                offenders.append(f"{path.relative_to(TESTS)}:{node.lineno}")
    assert offenders == []
