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
    assert (PROMPTS_DIR / "match_check.md").exists()


def test_match_check_prompt_has_a_version():
    prompt = load_prompt("match_check")
    assert prompt.version >= 1
    assert "for_next_pl_match" in prompt.text


def test_match_check_prompt_v3_rules():
    prompt = load_prompt("match_check")
    assert prompt.version == 3
    text = prompt.text.lower()
    for phrase in (
        "rest",
        "rotat",
        "suspension",
        "injury",
        "illness",
        "cup",
        "european",
        "national",
        "for_next_pl_match",
        "players",
    ):
        assert phrase in text
    for gone in ("event type", "events extracted", "<event type>"):
        assert gone not in text


def test_flow_sends_the_loaded_match_check_prompt():
    from app.extraction import flow

    assert flow.MATCH_CHECK_PROMPT == load_prompt("match_check")


@pytest.mark.parametrize(
    "keyword",
    [
        "out",
        "doubt",
        "benched",
        "confirmed_starter",
        "available",
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


def test_extraction_prompt_v7_rules():
    prompt = load_prompt("extraction")
    normalised = " ".join(prompt.text.lower().split())
    assert prompt.version == 7
    for phrase in (
        "own team",
        "hypothetical",
        "national team",
        "flag",
        "never `confirmed`",
        "`available`",
        "trained",
        "not suspended",
        "fatigue",
        "fake",
        "one of the five types",
    ):
        assert phrase in normalised, phrase
    for phrase in (
        "rested, rotated or left out of the squad",
        "with no injury or illness given gives no event",
        "a suspension served in such a match",
        "an injury or illness from any match keeps its",
        "misses a national-team, cup or european match through an injury or illness",
    ):
        assert phrase in normalised, phrase
    for gone in ("the match check decides later", "is extracted as the post says it"):
        assert gone not in normalised, gone
    assert "there is no positive availability type" not in normalised
