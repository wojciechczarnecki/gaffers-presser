import threading

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, SecretStr

from app.core.errors import ConfigError
from app.llm.chat import ChatModelSpec, single_model_config, structured_kwargs_for
from app.llm.models import ModelSettings
from app.llm.pricing import Price
from app.llm.structured import StructuredCaller, Usage, answer_from_raw, usage_from_raw
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.helpers import FixedClock

PRICES = {"fake/model": Price(input_per_million=1.0, output_per_million=2.0, checked="x")}
ROW = ModelSettings(
    reasoning_effort="none", temperature=True, structured_method="json_schema", checked="x"
)


class Verdict(BaseModel):
    label: str


def _caller(*responses, prices=PRICES, **kwargs) -> tuple[StructuredCaller, FakeChatModel]:
    fake = FakeChatModel(responses=list(responses), response_model="fake/answered")
    return StructuredCaller(fake, "fake/model", prices, FixedClock(), **kwargs), fake


def test_call_with_usage_returns_parsed_tokens_and_cost():
    caller, _ = _caller(Verdict(label="supports"))
    reply = caller.call_with_usage(Verdict, "system", "human")
    assert reply.parsed == Verdict(label="supports")
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (10, 5)
    assert reply.cost_usd == pytest.approx(10 / 1_000_000 * 1.0 + 5 / 1_000_000 * 2.0)


def test_call_returns_the_parsed_output_only():
    caller, fake = _caller(Verdict(label="related"))
    assert caller.call(Verdict, "system", "human") == Verdict(label="related")
    system, human = fake.received_messages[0]
    assert (system.content, human.content) == ("system", "human")


def test_costs_accumulate_across_calls():
    caller, _ = _caller(Verdict(label="a"), Verdict(label="b"))
    first = caller.call_with_usage(Verdict, "s", "h").cost_usd
    second = caller.call_with_usage(Verdict, "s", "h").cost_usd
    assert caller.cost_usd == pytest.approx(first + second)


def test_parse_failure_raises_and_counts():
    caller, _ = _caller({"wrong": 1}, {"wrong": 2}, {"wrong": 3})
    with pytest.raises(ValueError):
        caller.call(Verdict, "s", "h")
    assert caller.failures == 1


def test_a_transient_error_is_retried_inside():
    caller, _ = _caller(RuntimeError("down"), Verdict(label="supports"))
    assert caller.call(Verdict, "s", "h") == Verdict(label="supports")
    assert caller.clock.sleeps == [2.0]


def test_stopped_call_raises():
    stop = threading.Event()
    stop.set()
    caller, _ = _caller(Verdict(label="a"), stop_event=stop)
    with pytest.raises(RuntimeError):
        caller.call(Verdict, "s", "h")


def test_from_spec_passes_the_structured_method():
    fake = FakeChatModel(responses=[Verdict(label="a")])
    spec = ChatModelSpec(
        provider="openrouter",
        model="fake/model",
        chat_model=fake,
        structured_kwargs={"method": "json_schema"},
        settings=ROW,
    )
    caller = StructuredCaller.from_spec(spec, PRICES, FixedClock())
    assert caller.structured_kwargs == {"method": "json_schema"}
    assert caller.model == "fake/model"


def test_structured_kwargs_for_reads_the_catalogue():
    assert structured_kwargs_for("a/model", {"a/model": ROW}) == {"method": "json_schema"}
    assert structured_kwargs_for("b/other", {"a/model": ROW}) == {}


def test_single_model_config_has_no_fallback_and_rejects_unknown_models():
    config = single_model_config(SecretStr("k"), "a/model", {"a/model": ROW})
    assert (config.model, config.fallback_model, config.settings) == ("a/model", None, ROW)
    with pytest.raises(ConfigError):
        single_model_config(SecretStr("k"), "b/other", {"a/model": ROW})


def test_usage_and_answer_from_raw():
    raw = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 7,
            "output_tokens": 3,
            "total_tokens": 10,
            "output_token_details": {"reasoning": 2},
        },
        response_metadata={"cost": 0.5, "model_name": "m/x", "provider": "host", "id": "gen-1"},
    )
    assert usage_from_raw(raw) == Usage(7, 3, 2, 0.5)
    assert answer_from_raw(raw) == ("m/x", "host", "gen-1")
    assert usage_from_raw(object()) == Usage()


def test_usage_adds():
    assert Usage(1, 2, None, None) + Usage(3, None, None, 0.5) == Usage(4, 2, None, 0.5)
