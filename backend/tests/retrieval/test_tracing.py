import logging

from pydantic import SecretStr

from app.llm.tracing import TracingConfig
from app.retrieval.tracing import NULL_TRACER, LangfuseTracer, make_tracer
from tests.retrieval.fakes import FakeLangfuseClient


def test_make_tracer_builds_a_langfuse_tracer_offline():
    tracing = TracingConfig(
        public_key=SecretStr("pk-fake"), secret_key=SecretStr("sk-fake"), host="http://127.0.0.1:9"
    )
    assert isinstance(make_tracer(tracing), LangfuseTracer)


def test_langfuse_tracer_records_embedding_generation_offline():
    client = FakeLangfuseClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]

    tracer.embedding(model="a/embed", texts=["one"], input_tokens=5, cost_usd=1e-7)

    (observation,) = client.observations
    assert observation["as_type"] == "embedding"
    assert observation["model"] == "a/embed"
    assert observation["input"] == ["one"]
    assert observation["usage_details"] == {"input": 5}
    assert observation["cost_details"] == {"input": 1e-7}
    assert observation["level"] is None


def test_langfuse_tracer_records_a_failed_embedding_call():
    client = FakeLangfuseClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]

    tracer.embedding(
        model="a/embed", texts=["one"], input_tokens=None, cost_usd=None, error_class="Timeout"
    )

    (observation,) = client.observations
    assert (observation["level"], observation["status_message"]) == ("ERROR", "Timeout")
    assert observation["usage_details"] is None
    assert observation["cost_details"] is None


def test_langfuse_tracer_records_search_ids_per_mode():
    client = FakeLangfuseClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]

    tracer.search(
        query="saka injury",
        mode="hybrid",
        model="a/embed",
        filters={"exclude_reposts": True},
        ids_by_mode={"fulltext": [1, 2], "vector": [2]},
        failed_legs=(),
    )
    tracer.flush()

    (observation,) = client.observations
    assert observation["as_type"] == "retriever"
    assert observation["input"] == {
        "query": "saka injury",
        "mode": "hybrid",
        "filters": {"exclude_reposts": True},
    }
    assert observation["output"] == {"fulltext": ["1", "2"], "vector": ["2"]}
    assert observation["metadata"] == {"model": "a/embed", "failed_legs": []}
    assert client.flushed == 1


def test_langfuse_errors_are_logged_not_raised(caplog):
    tracer = LangfuseTracer(FakeLangfuseClient(fail=True))  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        tracer.embedding(model="a/embed", texts=["x"], input_tokens=1, cost_usd=None)
        tracer.search(
            query="q", mode="fulltext", model=None, filters={}, ids_by_mode={}, failed_legs=()
        )
        tracer.flush()
    assert caplog.text.count("retrieval tracing failed: ConnectionError") == 2
    assert "retrieval tracing flush failed: ConnectionError" in caplog.text


def test_no_tracing_logs_once(caplog):
    with caplog.at_level(logging.WARNING):
        tracer = make_tracer(None)
        tracer.embedding(model="a/embed", texts=["x"], input_tokens=1, cost_usd=None)
        tracer.search(
            query="q", mode="fulltext", model=None, filters={}, ids_by_mode={}, failed_legs=()
        )
        tracer.flush()
    assert tracer is NULL_TRACER
    warnings = [r for r in caplog.records if "retrieval tracing disabled" in r.getMessage()]
    assert len(warnings) == 1
