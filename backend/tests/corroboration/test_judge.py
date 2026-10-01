from datetime import UTC, datetime

import pytest

from app.content import load_prompt
from app.corroboration.judge import (
    PROMPT_VERSION,
    JudgeInput,
    JudgeOutput,
    build_judge,
    render_input,
)
from app.corroboration.schemas import Claim, PlayerRef, PostRef
from app.llm.pricing import Price
from app.llm.structured import StructuredCaller
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.helpers import FixedClock

PRICES = {
    "fake/model": Price(input_per_million=1.0, output_per_million=2.0, checked="x"),
    "fake/answered": Price(input_per_million=1.0, output_per_million=2.0, checked="x"),
}
T = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

PLAYER = PlayerRef("2026/27", 2, "Isak", "Newcastle")
ANCHOR = Claim(
    post=PostRef(1, "anchor_acc", None, False, T, "Isak has a groin problem, doubtful"),
    event_type="doubt",
    certainty="likely",
)
POST = PostRef(2, "lister", "origin_acc", True, T, "Isak withdrawn from the squad with injury")


def _caller(*responses):
    fake = FakeChatModel(responses=list(responses), response_model="fake/answered")
    return StructuredCaller(fake, "fake/model", PRICES, FixedClock()), fake


@pytest.mark.parametrize("label", ["supports", "contradicts", "related", "unrelated"])
def test_each_label_round_trips(label):
    caller, _ = _caller(JudgeOutput(label=label))
    reply = build_judge(caller).run(JudgeInput(PLAYER, ANCHOR, POST))
    assert reply.parsed.label == label
    assert reply.usage.input_tokens == 10
    assert reply.cost_usd is not None
    assert reply.answered_model == "fake/answered"


def test_the_prompt_and_the_human_message_carry_the_case():
    caller, fake = _caller(JudgeOutput(label="supports"))
    build_judge(caller).run(JudgeInput(PLAYER, ANCHOR, POST))
    system, human = fake.received_messages[0]
    assert system.content == load_prompt("corroboration_judge").text
    for expected in (
        "Isak",
        "Newcastle",
        "doubt",
        "likely",
        "Isak has a groin problem, doubtful",
        "@lister",
        "@origin_acc",
        "Isak withdrawn from the squad with injury",
    ):
        assert expected in human.content


def test_an_original_post_names_no_original_author():
    original = PostRef(3, "reporter", None, False, T, "Isak is fit")
    text = render_input(JudgeInput(PLAYER, ANCHOR, original))
    assert "Repost of" not in text
    assert "@reporter" in text


def test_a_failure_after_retries_raises():
    caller, _ = _caller(RuntimeError("down"), RuntimeError("down"), RuntimeError("down"))
    with pytest.raises(RuntimeError):
        build_judge(caller).run(JudgeInput(PLAYER, ANCHOR, POST))
    assert caller.failures == 1


def test_the_prompt_version_follows_the_prompt_header():
    assert PROMPT_VERSION == f"corroboration_judge@{load_prompt('corroboration_judge').version}"
