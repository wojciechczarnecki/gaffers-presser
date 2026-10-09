import ast
from pathlib import Path

from app.content import PROMPTS_DIR

PRESSER = Path(__file__).resolve().parents[2] / "app" / "presser"
CONTENT = PROMPTS_DIR.parent
DIACRITICS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")


def test_no_polish_string_literal_in_presser_code():
    files = sorted(PRESSER.rglob("*.py"))
    assert files
    offenders = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if DIACRITICS & set(node.value):
                    offenders.append(f"{path.relative_to(PRESSER)}:{node.lineno}")
    assert offenders == []


def test_product_content_files_exist():
    for relative in (
        "prompts/presser_writer.md",
        "presser_email.toml",
        "presser_glossary.toml",
        "presser_style_examples.md",
    ):
        assert (CONTENT / relative).is_file(), relative


def test_judge_prompt_exists():
    assert (CONTENT / "prompts" / "presser_judge.md").is_file()
