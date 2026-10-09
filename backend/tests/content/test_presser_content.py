import tomllib

from app.content import PROMPTS_DIR, load_prompt

CONTENT = PROMPTS_DIR.parent


def test_writer_prompt_loads_with_a_version():
    prompt = load_prompt("presser_writer")
    assert prompt.version >= 1
    assert "autosub" in prompt.text
    assert "1500" in prompt.text


def test_glossary_has_69_complete_terms():
    terms = tomllib.loads((CONTENT / "presser_glossary.toml").read_text(encoding="utf-8"))["term"]
    assert len(terms) == 69
    for term in terms:
        for field in ("term", "meaning", "example", "source"):
            assert term[field].strip(), field


def test_style_examples_have_three_examples():
    text = (CONTENT / "presser_style_examples.md").read_text(encoding="utf-8")
    assert sum(line.startswith("## Example") for line in text.splitlines()) == 3
