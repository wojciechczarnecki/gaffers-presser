import logging

from pydantic import SecretStr

from app.llm.tracing import TracingConfig
from app.retrieval.tracing import NULL_TRACER, LangfuseTracer, make_tracer


def test_langfuse_tracer_records_embedding_generation_offline():
    tracing = TracingConfig(
        public_key=SecretStr("pk-fake"), secret_key=SecretStr("sk-fake"), host="http://127.0.0.1:9"
    )
    tracer = make_tracer(tracing)
    assert isinstance(tracer, LangfuseTracer)

    tracer.embedding(model="a/embed", texts=["one"], input_tokens=5, cost_usd=1e-7)
    tracer.search(
        query="saka injury",
        mode="hybrid",
        model="a/embed",
        filters={"exclude_reposts": True},
        ids_by_mode={"fulltext": [1, 2], "vector": [2]},
        failed_legs=(),
    )
    tracer.flush()


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
