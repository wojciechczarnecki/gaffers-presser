import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openrouter.components import ChatResult
from pydantic import SecretStr
from sqlmodel import Session, select

from app.extraction.flow import build_flow
from app.extraction.linking import PlayerIndex
from app.extraction.models import Extraction
from app.extraction.schemas import PostInput
from app.extraction.service import ExtractionRuntime, extract_post
from app.extraction.tracing import run_config
from app.llm.chat import LlmConfig, build_chat_model
from app.llm.models import ModelSettings
from app.llm.pricing import Price
from app.tweets.models import Tweet
from tests.extraction.fakes import RecordingHandler

PAYLOAD = json.loads(
    (Path(__file__).parent / "payloads" / "openrouter_fallback_tool_call.json").read_text()
)
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
POST = PostInput(
    x_id=1,
    author_handle="reporter",
    text="Haaland is out.",
    created_at=NOW,
    is_repost=False,
    is_reply=False,
)
ROW = ModelSettings(
    reasoning_effort="none", temperature=True, structured_method="function_calling", checked="x"
)


class _Chat:
    def __init__(self, owner: "FakeSdkClient") -> None:
        self._owner = owner

    def send(self, **params):
        self._owner.calls.append(params)
        if self._owner.error is not None:
            raise self._owner.error
        # The pinned SDK returns its response model, which drops the top-level `provider`.
        return ChatResult.model_validate(self._owner.payload)


class FakeSdkClient:
    def __init__(self, payload=PAYLOAD, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []
        self.chat = _Chat(self)


def _spec(client: FakeSdkClient):
    config = LlmConfig(
        model="a/primary",
        fallback_model="b/fallback-model",
        api_key=SecretStr("dummy-key"),
        settings=ROW,
        fallback_settings=ROW,
    )
    spec = build_chat_model(config)
    spec.chat_model.client = client
    return spec


class _Clock:
    def __init__(self) -> None:
        self.ticks = 0

    def now(self) -> datetime:
        self.ticks += 1
        return NOW + timedelta(seconds=self.ticks)

    def sleep(self, seconds: float) -> None:
        pass


def _runtime(spec) -> ExtractionRuntime:
    return ExtractionRuntime(
        provider=spec.provider,
        model=spec.model,
        make_spec=lambda: spec,
        tracing=None,
        prices={
            "b/fallback-model": Price(input_per_million=1.0, output_per_million=2.0, checked="x")
        },
        aliases=([], []),
        fallback_model="b/fallback-model",
    )


def _seed(db_session) -> None:
    db_session.add(
        Tweet(
            x_id=1,
            author_handle="reporter",
            text="Haaland is out.",
            created_at=NOW,
            first_fetched_at=NOW,
            source="list",
            is_repost=False,
            is_reply=False,
            raw={},
        )
    )
    db_session.commit()


def test_fallback_answer_recorded(db_session):
    _seed(db_session)
    client = FakeSdkClient()
    spec = _spec(client)

    outcome = extract_post(
        db_session.get_bind(), _runtime(spec), POST, _Clock(), threading.Event(), None, False
    )

    assert outcome.status == "extracted"
    with Session(db_session.get_bind()) as session:
        row = session.exec(select(Extraction)).one()
    assert (row.provider, row.model) == ("openrouter", "b/fallback-model")
    assert row.input_tokens == 120
    assert row.output_tokens == 40
    assert row.cost_usd == pytest.approx(120 / 1e6 * 1.0 + 40 / 1e6 * 2.0)
    (call,) = client.calls
    assert call["models"] == ["a/primary", "b/fallback-model"]
    assert call["model"] == "a/primary"
    assert call["reasoning"] == {"effort": "none"}
    assert call["provider"] == {"require_parameters": True}
    assert call["tool_choice"]["function"]["name"] == "ExtractionOutput"


def test_generation_id_and_reasoning_tokens_reach_the_flow_result():
    flow = build_flow(_spec(FakeSdkClient()), PlayerIndex([], [], [], []))

    result = flow.run(POST, config={})

    assert result.host is None  # filled later from the generation lookup (PLAN 005, D1)
    assert result.answered_model == "b/fallback-model"
    assert result.generation_id == "gen-synthetic-0001"
    assert result.usage.reasoning_tokens == 7
    assert result.usage.reported_cost_usd == pytest.approx(0.000123)
    assert [e.mention for e in result.events] == ["Haaland"]


def test_usage_and_callback_through_chat_openrouter():
    handler = RecordingHandler()
    flow = build_flow(_spec(FakeSdkClient()), PlayerIndex([], [], [], []))
    config = run_config(1, "extraction@1", "openrouter", "a/primary", handler)

    flow.run(POST, config=config)

    (start,) = handler.chat_model_starts
    assert start["metadata"]["x_id"] == 1
    assert start["metadata"]["model"] == "a/primary"
    (end,) = handler.llm_ends
    assert end["usage"]["input_tokens"] == 120
    assert end["usage"]["output_tokens"] == 40
    assert end["llm_output"]["model_name"] == "b/fallback-model"


def test_provider_error_gives_three_attempts_then_a_failed_row(db_session):
    _seed(db_session)
    client = FakeSdkClient(error=RuntimeError("upstream 503"))
    spec = _spec(client)

    outcome = extract_post(
        db_session.get_bind(), _runtime(spec), POST, _Clock(), threading.Event(), None, False
    )

    assert outcome.status == "failed"
    assert len(client.calls) == 3
    with Session(db_session.get_bind()) as session:
        row = session.exec(select(Extraction)).one()
    assert (row.status, row.model, row.attempts) == ("failed", "a/primary", 3)
    assert row.error_class == "RuntimeError"
