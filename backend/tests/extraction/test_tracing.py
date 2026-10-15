from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app.extraction.flow import ExtractionOutputError, build_flow
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord
from app.extraction.schemas import Disambiguation, ExtractedEvent, ExtractionOutput, PostInput
from app.extraction.tracing import (
    NULL_EXTRACTION_TRACER,
    LangfuseExtractionTracer,
    make_extraction_tracer,
)
from app.llm.chat import ChatModelSpec
from app.llm.pricing import Price
from app.llm.tracing import TracingConfig
from tests.corroboration.fakes import FakeLangfuseClient
from tests.extraction.fakes import FakeChatModel, RecordingExtractionTracer

SEASON = "2026/27"

SMITH_ONE = PlayerRecord(
    season=SEASON, fpl_id=2, web_name="Smith", first_name="John", second_name="Smith", team_fpl_id=1
)
SMITH_TWO = PlayerRecord(
    season=SEASON,
    fpl_id=3,
    web_name="J.Smith",
    first_name="Jake",
    second_name="Smith",
    team_fpl_id=2,
)
TEAM_ONE = TeamRecord(season=SEASON, fpl_id=1, name="Team One", short_name="ONE")
TEAM_TWO = TeamRecord(season=SEASON, fpl_id=2, name="Team Two", short_name="TWO")
INDEX = PlayerIndex([SMITH_ONE, SMITH_TWO], [TEAM_ONE, TEAM_TWO])

POST = PostInput(
    x_id=42,
    author_handle="reporter",
    text="Smith starts today.",
    created_at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
    is_repost=False,
    is_reply=False,
)


PRICES = {"fake": Price(input_per_million=1.0, output_per_million=2.0, checked="x")}
OFFLINE = TracingConfig(
    public_key=SecretStr("pk-fake"), secret_key=SecretStr("sk-fake"), host="http://127.0.0.1:9"
)


def _flow(*responses) -> tuple[RecordingExtractionTracer, object]:
    spec = ChatModelSpec(
        provider="fake", model="fake", chat_model=FakeChatModel(responses=list(responses))
    )
    tracer = RecordingExtractionTracer()
    return tracer, build_flow(spec, INDEX, tracer, PRICES)


def _smith_output() -> ExtractionOutput:
    return ExtractionOutput(
        events=[
            ExtractedEvent(
                player="Smith", team=None, event_type="confirmed_starter", certainty="confirmed"
            )
        ]
    )


def test_one_generation_per_llm_call_priced_from_prices_toml():
    tracer, flow = _flow(_smith_output(), Disambiguation(fpl_id=SMITH_TWO.fpl_id))

    result = flow.run(POST)

    assert result.events[0].player_fpl_id == SMITH_TWO.fpl_id
    extraction, disambiguation = tracer.generations
    assert "Smith starts today." in extraction["input"]
    assert "Candidates:" in disambiguation["input"]
    assert '"fpl_id":3' in disambiguation["output"]
    for generation in tracer.generations:
        assert generation["model"] == "fake"
        assert generation["usage"].input_tokens == 10
        assert generation["usage"].output_tokens == 5
        assert generation["cost_usd"] == pytest.approx(10 / 1e6 * 1.0 + 5 / 1e6 * 2.0)
        assert generation["error_class"] is None
        assert generation["latency_seconds"] >= 0
    assert tracer.spans == []


def test_failed_call_is_traced_with_its_error_class():
    tracer, flow = _flow(RuntimeError("upstream 503"))

    with pytest.raises(RuntimeError):
        flow.run(POST)

    (generation,) = tracer.generations
    assert generation["error_class"] == "RuntimeError"
    assert generation["output"] is None
    assert generation["cost_usd"] is None
    assert generation["latency_seconds"] >= 0


class _UnstructurableModel(FakeChatModel):
    def with_structured_output(self, schema, **kwargs):
        raise NotImplementedError("schema rejected")


def test_a_call_that_cannot_be_built_is_traced():
    spec = ChatModelSpec(
        provider="fake", model="fake", chat_model=_UnstructurableModel(responses=[])
    )
    tracer = RecordingExtractionTracer()
    flow = build_flow(spec, INDEX, tracer, PRICES)

    with pytest.raises(NotImplementedError):
        flow.run(POST)

    (generation,) = tracer.generations
    assert generation["error_class"] == "NotImplementedError"


def test_invalid_output_is_traced_as_an_error_with_its_cost():
    tracer, flow = _flow({"events": "not-a-list"})

    with pytest.raises(ExtractionOutputError):
        flow.run(POST)

    (generation,) = tracer.generations
    assert generation["error_class"] is not None
    assert generation["cost_usd"] is not None


def test_failed_disambiguation_is_traced_and_the_flow_goes_on():
    tracer, flow = _flow(_smith_output(), RuntimeError("boom"))

    result = flow.run(POST)

    assert result.events[0].player_fpl_id is None
    assert [g["error_class"] for g in tracer.generations] == [None, "RuntimeError"]


def test_null_tracer_without_keys():
    assert make_extraction_tracer(None) is NULL_EXTRACTION_TRACER


def test_tracer_built_offline():
    tracer = make_extraction_tracer(OFFLINE)

    assert isinstance(tracer, LangfuseExtractionTracer)
    tracer.flush()


def test_tracer_that_cannot_start_falls_back_to_null(monkeypatch, caplog):
    def broken(tracing):
        raise ValueError("langfuse client cannot start with secret sk-lf-sentinel")

    monkeypatch.setattr("app.llm.tracing.make_client", broken)

    with caplog.at_level("ERROR"):
        tracer = make_extraction_tracer(OFFLINE)

    assert tracer is NULL_EXTRACTION_TRACER
    assert "ValueError" in caplog.text
    assert "sk-lf-sentinel" not in caplog.text


class _Attributes:
    def __init__(self, calls: list, fail: bool = False, **kwargs) -> None:
        self._calls = calls
        self._fail = fail
        calls.append(("enter", kwargs))

    def __enter__(self):
        if self._fail:
            raise ValueError("bad session id")
        return self

    def __exit__(self, *exc_info) -> None:
        self._calls.append(("exit", {}))


def test_session_groups_an_evaluation_run_and_tags_it(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "app.llm.tracing.propagate_attributes", lambda **kwargs: _Attributes(calls, **kwargs)
    )
    client = FakeLangfuseClient()
    tracer = LangfuseExtractionTracer(client)

    with tracer.span("extraction", {"x_id": "1"}, session_id="eval-run-7"):
        assert client.stack == ["extraction"]

    assert calls == [("enter", {"session_id": "eval-run-7", "tags": ["eval-run-7"]}), ("exit", {})]
    assert client.observations[0]["ended"] is True


def test_no_session_outside_an_evaluation(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "app.llm.tracing.propagate_attributes", lambda **kwargs: _Attributes(calls, **kwargs)
    )

    with LangfuseExtractionTracer(FakeLangfuseClient()).span("extraction", {"x_id": 1}):
        pass

    assert calls == []


def test_a_session_that_cannot_be_set_leaves_the_work_untraced(monkeypatch, caplog):
    calls: list = []
    monkeypatch.setattr(
        "app.llm.tracing.propagate_attributes",
        lambda **kwargs: _Attributes(calls, fail=True, **kwargs),
    )
    client = FakeLangfuseClient()

    with caplog.at_level("ERROR"):
        with LangfuseExtractionTracer(client).span("extraction", {}, session_id="r") as span:
            ran = True

    assert ran and span.trace_id is None
    assert client.observations == []
    assert "extraction tracing failed: ValueError" in caplog.text
