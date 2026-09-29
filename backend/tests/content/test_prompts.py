import pytest

from app.content import PROMPTS_DIR, PromptError, load_prompt


def test_load_prompt_parses_version_header():
    prompt = load_prompt("extraction")
    assert prompt.name == "extraction"
    # Bumped on every prompt change (spec 005 AC11), so only its shape is checked here.
    assert isinstance(prompt.version, int) and prompt.version >= 1
    assert "player" in prompt.text.lower()


def test_load_prompt_missing_file_raises():
    with pytest.raises(PromptError):
        load_prompt("does-not-exist")


def test_load_prompt_missing_header_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("app.content.PROMPTS_DIR", tmp_path)
    (tmp_path / "broken.md").write_text("no header here\njust text", encoding="utf-8")
    with pytest.raises(PromptError):
        load_prompt("broken")


def test_prompts_directory_has_both_files():
    assert (PROMPTS_DIR / "extraction.md").exists()
    assert (PROMPTS_DIR / "link_disambiguation.md").exists()


@pytest.mark.parametrize(
    "keyword",
    [
        "out",
        "doubt",
        "benched",
        "confirmed_starter",
        "confirmed",
        "likely",
        "rumour",
        "international",
        "national",
        "women",
        "cup",
        "european",
        "next premier league",
    ],
)
def test_extraction_prompt_states_relevance_rule(keyword):
    prompt = load_prompt("extraction")
    normalised = " ".join(prompt.text.lower().split())
    assert keyword in normalised


@pytest.mark.parametrize("name", ["retrieval_query", "retrieval_relevance"])
def test_retrieval_prompts_load_with_a_version(name):
    prompt = load_prompt(name)
    assert prompt.version >= 1
    assert prompt.text.strip()
