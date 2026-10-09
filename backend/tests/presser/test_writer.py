import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.llm.pricing import Price
from app.llm.structured import StructuredCaller, Usage
from app.presser.facts import FactSheet
from app.presser.tracing import LangfusePresserTracer
from app.presser.writer import (
    PROMPT_VERSION,
    WRITER_PROMPT,
    PresserDraft,
    PreviousPresser,
    WriterInput,
    build_writer,
    load_glossary,
    load_style_examples,
)
from tests.corroboration.fakes import FakeLangfuseClient
from tests.delivery.fakes import FixedClock
from tests.extraction.fakes import FakeChatModel

PRICES = {"fake/model": Price(input_per_million=1.0, output_per_million=2.0, checked="x")}


def facts() -> FactSheet:
    return FactSheet.model_validate(
        {
            "league": "Synthetic League",
            "season": "2026/27",
            "gameweek": 5,
            "managers": 1,
            "average_points": 50.0,
            "winners": [{"manager": "Bartas", "points": 50, "transfers_cost": 0, "net_points": 50}],
            "flops": [{"manager": "Bartas", "points": 50, "transfers_cost": 0, "net_points": 50}],
            "empty_sections": ["captaincy", "bench_transfers_chips", "table"],
        }
    )


def caller(*responses):
    fake = FakeChatModel(responses=list(responses))
    return StructuredCaller(fake, "fake/model", PRICES, FixedClock()), fake


def test_prompt_version_label():
    assert PROMPT_VERSION == f"presser_writer@{WRITER_PROMPT.version}"


def test_input_holds_facts_glossary_examples_previous():
    structured_caller, fake = caller(PresserDraft(text="Presser"))
    previous = [PreviousPresser(3, "Wcześniejszy tekst o Bartasie"), PreviousPresser(4, "GW4 text")]
    reply = build_writer(structured_caller).run(WriterInput(facts(), previous))
    assert reply.parsed.text == "Presser"
    system, human = fake.received_messages[0]
    assert isinstance(system, SystemMessage) and isinstance(human, HumanMessage)
    assert system.content == WRITER_PROMPT.text
    assert facts().model_dump_json(indent=1) in human.content
    assert load_glossary().splitlines()[0] in human.content
    assert load_style_examples().splitlines()[0] in human.content
    assert "GW4:\nGW4 text" in human.content
    assert human.content.index("GW3:") < human.content.index("GW4:")


def test_first_presser_says_none():
    structured_caller, fake = caller(PresserDraft(text="Presser"))
    build_writer(structured_caller).run(WriterInput(facts(), []))
    assert fake.received_messages[0][1].content.rstrip().endswith("none")


def test_glossary_lines_have_three_parts():
    lines = load_glossary().splitlines()
    assert len(lines) == 70
    assert all(line.count(" — ") >= 2 for line in lines)


def test_trace_created():
    client = FakeLangfuseClient()
    tracer = LangfusePresserTracer(client)
    with tracer.span("presser", {"gameweek": 5}) as span:
        tracer.generation(
            model="fake/model",
            input="human",
            output="text",
            usage=Usage(input_tokens=10, output_tokens=5),
            cost_usd=0.0002,
            latency_seconds=1.5,
        )
        span.update(output={"chars": 4})
    (generation,) = client.named("presser-writer")
    assert generation["as_type"] == "generation"
    assert generation["model"] == "fake/model"
    assert generation["usage_details"] == {"input": 10, "output": 5}
    assert generation["cost_details"] == {"total": 0.0002}
    assert generation["metadata"] == {"latency_seconds": 1.5}
    assert generation["parent"] == "presser"
    (root,) = client.named("presser")
    assert root["updates"] == [{"output": {"chars": 4}}]


def test_failed_generation_is_marked_and_tracing_failure_is_swallowed(caplog):
    client = FakeLangfuseClient(fail=True)
    tracer = LangfusePresserTracer(client)
    with caplog.at_level("ERROR"):
        tracer.generation(
            model="m",
            input="i",
            output=None,
            usage=None,
            cost_usd=None,
            latency_seconds=None,
            error_class="TimeoutError",
        )
        with tracer.span("presser", {}):
            pass
        tracer.flush()
    assert "ConnectionError" in caplog.text


@pytest.mark.parametrize("error", ["TimeoutError"])
def test_error_generation_level(error):
    client = FakeLangfuseClient()
    LangfusePresserTracer(client).generation(
        model="m",
        input="i",
        output=None,
        usage=None,
        cost_usd=None,
        latency_seconds=2.0,
        error_class=error,
    )
    (generation,) = client.named("presser-writer")
    assert (generation["level"], generation["status_message"]) == ("ERROR", error)
