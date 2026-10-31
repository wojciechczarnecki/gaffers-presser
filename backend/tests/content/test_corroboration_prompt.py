import pytest

from app.content import load_prompt


def test_the_judge_prompt_loads_with_a_version():
    prompt = load_prompt("corroboration_judge")
    assert prompt.version >= 1
    assert prompt.text.strip()


@pytest.mark.parametrize(
    "keyword",
    [
        "supports",
        "contradicts",
        "related",
        "unrelated",
        "next premier league",
        "national",
        "cup",
        "european",
        "women",
    ],
)
def test_the_judge_prompt_states_the_labels_and_the_relevance_rule(keyword):
    normalised = " ".join(load_prompt("corroboration_judge").text.lower().split())
    assert keyword in normalised


def test_judge_prompt_v2_has_five_anchor_types():
    prompt = load_prompt("corroboration_judge")
    assert prompt.version == 2
    for event_type in ("out", "doubt", "benched", "confirmed_starter", "available"):
        assert f"`{event_type}`" in prompt.text
    section = prompt.text.split("## When the anchor is `available`")[1]
    assert "`contradicts`" in section and "`related`" in section
