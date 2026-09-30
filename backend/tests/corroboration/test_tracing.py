import pytest

from app.corroboration.tracing import (
    NULL_CORROBORATION_TRACER,
    LangfuseCorroborationTracer,
    make_corroboration_tracer,
)
from app.llm.structured import Usage
from tests.corroboration.fakes import FakeLangfuseClient


def test_the_root_span_carries_the_input_and_the_output_update():
    client = FakeLangfuseClient()
    tracer = LangfuseCorroborationTracer(client)
    with tracer.span("corroboration", {"player": "Saka", "as_of": "t"}) as span:
        span.update(output={"anchor": 1, "grade": "high"})
        assert span.trace_id == "trace-1"
    (root,) = client.named("corroboration")
    assert root["as_type"] == "chain"
    assert root["input"] == {"player": "Saka", "as_of": "t"}
    assert root["updates"] == [{"output": {"anchor": 1, "grade": "high"}}]
    assert root["ended"] is True
    assert client.stack == []


def test_a_generation_records_model_usage_and_cost():
    client = FakeLangfuseClient()
    tracer = LangfuseCorroborationTracer(client)
    with tracer.span("corroboration", {}):
        tracer.generation(
            model="fake/model",
            input="human",
            output="supports",
            usage=Usage(input_tokens=10, output_tokens=5),
            cost_usd=0.0002,
        )
    (generation,) = client.named("corroboration-judge")
    assert generation["as_type"] == "generation"
    assert generation["model"] == "fake/model"
    assert generation["usage_details"] == {"input": 10, "output": 5}
    assert generation["cost_details"] == {"total": 0.0002}
    assert generation["parent"] == "corroboration"
    assert generation["ended"] is True


def test_a_failed_generation_is_marked_as_an_error():
    client = FakeLangfuseClient()
    LangfuseCorroborationTracer(client).generation(
        model="fake/model",
        input="human",
        output=None,
        usage=None,
        cost_usd=None,
        error_class="TimeoutError",
    )
    (generation,) = client.named("corroboration-judge")
    assert (generation["level"], generation["status_message"]) == ("ERROR", "TimeoutError")


def test_retrieval_observations_start_while_the_root_is_current():
    client = FakeLangfuseClient()
    tracer = LangfuseCorroborationTracer(client)
    with tracer.span("corroboration", {}):
        retrieval = tracer.retrieval()
        retrieval.embedding(model="m", texts=["q"], input_tokens=3, cost_usd=None)
        retrieval.search(
            query="q", mode="hybrid", model="m", filters={}, ids_by_mode={}, failed_legs=()
        )
    assert [o["parent"] for o in client.named("embedding")] == ["corroboration"]
    assert [o["parent"] for o in client.named("retrieval-search")] == ["corroboration"]


def test_a_failing_client_never_raises_and_the_body_still_runs():
    client = FakeLangfuseClient(fail=True)
    tracer = LangfuseCorroborationTracer(client)
    ran = []
    with tracer.span("corroboration", {}) as span:
        span.update(output={})
        assert span.trace_id is None
        ran.append(1)
    tracer.generation(model="m", input="i", output="o", usage=None, cost_usd=None)
    tracer.retrieval().search(
        query="q", mode="hybrid", model=None, filters={}, ids_by_mode={}, failed_legs=()
    )
    tracer.flush()
    assert ran == [1]


def test_an_exception_in_the_body_propagates_and_the_span_closes():
    client = FakeLangfuseClient()
    tracer = LangfuseCorroborationTracer(client)
    with pytest.raises(ValueError):
        with tracer.span("corroboration", {}):
            raise ValueError("boom")
    assert client.stack == []
    assert client.named("corroboration")[0]["ended"] is True


def test_flush_reaches_the_client():
    client = FakeLangfuseClient()
    LangfuseCorroborationTracer(client).flush()
    assert client.flushed == 1


def test_the_null_tracer_does_nothing():
    with NULL_CORROBORATION_TRACER.span("corroboration", {"a": 1}) as span:
        span.update(output={})
        assert span.trace_id is None
    NULL_CORROBORATION_TRACER.generation(
        model="m", input="i", output="o", usage=None, cost_usd=None
    )
    NULL_CORROBORATION_TRACER.retrieval().search(
        query="q", mode="hybrid", model=None, filters={}, ids_by_mode={}, failed_legs=()
    )
    NULL_CORROBORATION_TRACER.flush()


def test_without_langfuse_keys_the_tracer_is_null():
    assert make_corroboration_tracer(None) is NULL_CORROBORATION_TRACER
