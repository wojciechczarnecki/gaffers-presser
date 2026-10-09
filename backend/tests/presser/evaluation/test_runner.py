from datetime import UTC, datetime

import pytest

from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.presser.evaluation.judge import (
    JudgedClaim,
    JudgeInput,
    JudgeVerdict,
    PresserJudge,
    build_presser_judge,
    render_input,
)
from app.presser.evaluation.runner import (
    EvaluationError,
    default_result_path,
    run_evaluation,
)
from app.presser.writer import PresserDraft, PreviousPresser, Writer, build_writer
from tests.delivery.fakes import FixedClock
from tests.extraction.fakes import FakeChatModel
from tests.presser.evaluation.factories import case, valid_set

NOW = datetime(2026, 10, 9, tzinfo=UTC)
WRITER_MODEL = "openai/gpt-6-luna"
JUDGE_MODEL = "openai/gpt-6.1-sol"


def writer_for(*responses) -> tuple[Writer, FakeChatModel]:
    fake = FakeChatModel(responses=list(responses))
    return build_writer(StructuredCaller(fake, WRITER_MODEL, load_prices(), FixedClock())), fake


def judge_for(*responses) -> tuple[PresserJudge, FakeChatModel]:
    fake = FakeChatModel(responses=list(responses))
    return (
        build_presser_judge(StructuredCaller(fake, JUDGE_MODEL, load_prices(), FixedClock())),
        fake,
    )


def claims(*labels: str) -> JudgeVerdict:
    return JudgeVerdict(
        claims=[JudgedClaim(claim=f"claim {n}", label=label) for n, label in enumerate(labels)]
    )


def run(cases, writer, judge, split="dev"):
    return run_evaluation(cases, writer, judge, split, WRITER_MODEL, JUDGE_MODEL, NOW)


ONE = [case("c1", "dev")]


def test_faithfulness_is_supported_over_all_claims():
    writer, _ = writer_for(PresserDraft(text="Tekst"))
    judge, _ = judge_for(claims("supported", "supported", "unsupported"))
    result = run(ONE, writer, judge)
    (record,) = result["pressers"]
    assert record["faithfulness"] == pytest.approx(2 / 3)
    assert [c["label"] for c in record["claims"]] == ["supported", "supported", "unsupported"]
    assert all(c["owner_label"] is None for c in record["claims"])
    assert record["style"] is None
    assert result["totals"]["faithfulness"] == pytest.approx(2 / 3)
    assert result["split"] == "dev" and result["model"] == WRITER_MODEL
    assert result["judge_model"] == JUDGE_MODEL
    assert result["prompt_version"].startswith("presser_writer@")
    assert result["judge_prompt_version"].startswith("presser_judge@")


def test_no_claims_counts_as_fully_faithful():
    writer, _ = writer_for(PresserDraft(text="Tekst"))
    judge, _ = judge_for(claims())
    assert run(ONE, writer, judge)["pressers"][0]["faithfulness"] == 1.0


@pytest.mark.parametrize(("length", "within"), [(1500, True), (1501, False)])
def test_length_flag_at_the_limit(length, within):
    writer, _ = writer_for(PresserDraft(text="x" * length))
    judge, _ = judge_for(claims("supported"))
    result = run(ONE, writer, judge)
    record = result["pressers"][0]
    assert (record["length"], record["within_limit"]) == (length, within)
    assert result["totals"]["within_limit_share"] == (1.0 if within else 0.0)


def test_usage_cost_and_latency_recorded():
    writer, _ = writer_for(PresserDraft(text="Tekst"))
    judge, _ = judge_for(claims("supported"))
    record = run(ONE, writer, judge)["pressers"][0]
    assert (record["input_tokens"], record["output_tokens"]) == (10, 5)
    assert record["cost_usd"] > 0 and record["judge_cost_usd"] > 0
    assert record["latency_seconds"] >= 0


def test_writer_error_skips_the_judge():
    writer, _ = writer_for(ValueError("a"), ValueError("b"), ValueError("c"))
    judge, judge_model = judge_for(claims("supported"))
    result = run(ONE, writer, judge)
    record = result["pressers"][0]
    assert record["error_class"] == "ValueError"
    assert record["text"] is None and record["faithfulness"] is None
    assert judge_model.received_messages == []
    assert result["totals"]["errored"] == 1
    assert result["totals"]["faithfulness"] is None


def test_judge_error_keeps_the_text_and_leaves_faithfulness_null():
    writer, _ = writer_for(PresserDraft(text="Tekst"))
    judge, _ = judge_for(ValueError("a"), ValueError("b"), ValueError("c"))
    record = run(ONE, writer, judge)["pressers"][0]
    assert record["text"] == "Tekst"
    assert record["error_class"] == "ValueError"
    assert record["faithfulness"] is None and record["claims"] == []


def test_only_the_requested_split_runs():
    cases = valid_set()
    in_dev = [c for c in cases if c.split == "dev"]
    writer, fake = writer_for(*[PresserDraft(text="t")] * len(in_dev))
    judge, _ = judge_for(*[claims("supported")] * len(in_dev))
    result = run(cases, writer, judge, "dev")
    assert [p["case_id"] for p in result["pressers"]] == [c.id for c in in_dev]
    assert len(fake.received_messages) == len(in_dev)


def test_an_empty_split_is_an_error():
    writer, _ = writer_for()
    judge, _ = judge_for()
    with pytest.raises(EvaluationError):
        run([case("c1", "test")], writer, judge, "dev")


def test_judge_input_holds_sheet_previous_and_presser():
    judge, fake = judge_for(claims("supported"))
    item = JudgeInput(case("c").facts, [PreviousPresser(3, "Poprzedni tekst")], "Sprawdzany tekst")
    judge.run(item)
    human = fake.received_messages[0][1].content
    assert item.facts.model_dump_json(indent=1) in human
    assert "GW3:\nPoprzedni tekst" in human
    assert human.endswith("Sprawdzany tekst")
    assert render_input(JudgeInput(item.facts, [], "t")).count("none") == 1


def test_default_result_path_replaces_the_slash():
    assert default_result_path("test", "openai/gpt-6-luna").name == "test-openai-gpt-6-luna.json"
