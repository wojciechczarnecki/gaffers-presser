import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.errors import ConfigError
from app.extraction.flow import PROMPT_VERSION
from app.extraction.models import Extraction
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import ExtractedEvent, ExtractionOutput, PostInput
from app.extraction.service import (
    RETRY_BACKOFF_SECONDS,
    ExtractionRuntime,
    extract_post,
    load_reference_files,
)
from app.extraction.store import current_extraction
from app.llm.pricing import Price, load_prices
from app.tweets.models import Tweet
from tests.extraction.fakes import FakeChatModel, RecordingHandler

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
PRICES = {"fake-model": Price(input_per_million=1.0, output_per_million=2.0, checked="x")}

POST = PostInput(
    x_id=1,
    author_handle="reporter",
    text="Haaland starts today.",
    created_at=NOW,
    is_repost=False,
    is_reply=False,
)


class RecordingClock:
    def __init__(self, times: list[datetime]):
        self._times = list(times)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._times.pop(0)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _seed_tweet(session, x_id: int = 1, first_fetched_at: datetime = NOW) -> None:
    session.add(
        Tweet(
            x_id=x_id,
            author_handle="reporter",
            text=POST.text,
            created_at=POST.created_at,
            first_fetched_at=first_fetched_at,
            source="list",
            is_repost=False,
            is_reply=False,
            raw={},
        )
    )
    session.commit()


def _runtime(*responses, prices=None) -> ExtractionRuntime:
    fake = FakeChatModel(responses=list(responses))
    spec = ChatModelSpec(provider="fake", model="fake-model", chat_model=fake)
    return ExtractionRuntime(
        provider="fake",
        model="fake-model",
        make_spec=lambda: spec,
        tracing=None,
        prices=prices or {},
        aliases=([], []),
    )


def _row(db_session, extraction_id: int) -> Extraction:
    with Session(db_session.get_bind()) as session:
        return session.exec(select(Extraction).where(Extraction.id == extraction_id)).one()


def _extract(db_session, runtime, clock, handler=None):
    return extract_post(
        db_session.get_bind(),
        runtime,
        POST,
        clock,
        threading.Event(),
        handler,
        record_latency=False,
    )


def test_success_first_attempt(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(ExtractionOutput(events=[]))
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=False
    )

    assert outcome is not None
    assert outcome.status == "extracted"
    assert outcome.events == 0
    assert clock.sleeps == []


def test_retries_then_success(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(RuntimeError("boom"), ExtractionOutput(events=[]))
    clock = RecordingClock([NOW, NOW + timedelta(seconds=3)])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=False
    )

    assert outcome is not None
    assert outcome.status == "extracted"
    assert clock.sleeps == [RETRY_BACKOFF_SECONDS[0]]


def test_retries_exhausted_stores_failed(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(RuntimeError("first"), TimeoutError("second"), RuntimeError("third"))
    clock = RecordingClock([NOW, NOW + timedelta(seconds=10)])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=False
    )

    assert outcome is not None
    assert outcome.status == "failed"
    assert clock.sleeps == list(RETRY_BACKOFF_SECONDS)

    current = current_extraction(db_session, 1)
    assert current is None  # only "extracted" counts as current


def test_stop_between_attempts_stores_nothing(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(RuntimeError("boom"), ExtractionOutput(events=[]))
    stop_event = threading.Event()

    class StoppingClock(RecordingClock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            stop_event.set()

    stopping_clock = StoppingClock([NOW])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, stopping_clock, stop_event, None, record_latency=False
    )

    assert outcome is None
    assert current_extraction(db_session, 1) is None


def test_cost_null_without_price(db_session):
    _seed_tweet(db_session)
    output = ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="out", certainty="confirmed")
        ]
    )
    runtime = _runtime(output, prices={})
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=False
    )

    assert outcome is not None
    assert outcome.cost_usd is None


def test_cost_from_price_table(db_session):
    _seed_tweet(db_session)
    output = ExtractionOutput(events=[])
    runtime = _runtime(output, prices=PRICES)
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=False
    )

    assert outcome is not None
    # FakeChatModel reports 10 input / 5 output tokens for its one call.
    assert outcome.cost_usd == (10 / 1_000_000 * 1.0) + (5 / 1_000_000 * 2.0)


def test_worker_extraction_records_latency(db_session):
    first_fetched_at = NOW
    _seed_tweet(db_session, first_fetched_at=first_fetched_at)
    runtime = _runtime(ExtractionOutput(events=[]))
    finished_at = NOW + timedelta(seconds=7)
    clock = RecordingClock([NOW, finished_at])

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=True
    )

    assert outcome is not None
    current = current_extraction(db_session, 1)
    assert current is not None
    # latency is on the stored extraction row; re-read it through the engine directly.
    with Session(db_session.get_bind()) as session:
        row = session.exec(select(Extraction).where(Extraction.id == current.extraction_id)).one()
        assert row.latency_seconds == 7.0


def test_credentials_never_logged_or_stored(db_session, caplog):
    _seed_tweet(db_session)
    sentinel = "sk-super-secret-sentinel"
    runtime = _runtime(
        RuntimeError(f"auth failed with key {sentinel}"),
        RuntimeError(f"auth failed with key {sentinel}"),
        RuntimeError(f"auth failed with key {sentinel}"),
    )
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    with caplog.at_level("WARNING"):
        outcome = extract_post(
            db_session.get_bind(),
            runtime,
            POST,
            clock,
            threading.Event(),
            None,
            record_latency=False,
        )

    assert outcome is not None
    assert outcome.status == "failed"
    assert sentinel not in caplog.text
    with Session(db_session.get_bind()) as session:
        row = session.exec(select(Extraction).where(Extraction.id == outcome.extraction_id)).one()
        assert sentinel not in (row.error_class or "")


def test_extracted_row_holds_attempts_tokens_and_cost(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(RuntimeError("boom"), ExtractionOutput(events=[]), prices=PRICES)
    clock = RecordingClock([NOW, NOW + timedelta(seconds=3)])

    outcome = _extract(db_session, runtime, clock)

    row = _row(db_session, outcome.extraction_id)
    assert row.status == "extracted"
    assert row.attempts == 2
    assert row.error_class is None
    # Tokens of the successful call only: FakeChatModel reports 10 in / 5 out per call.
    assert (row.input_tokens, row.output_tokens) == (10, 5)
    assert row.cost_usd == pytest.approx(10 / 1_000_000 * 1.0 + 5 / 1_000_000 * 2.0)
    assert (row.provider, row.model, row.prompt_version) == ("fake", "fake-model", PROMPT_VERSION)
    assert (row.started_at, row.finished_at) == (NOW, NOW + timedelta(seconds=3))


@pytest.mark.parametrize(
    ("error", "error_class"),
    [
        (RuntimeError("provider error"), "RuntimeError"),
        (TimeoutError("timed out"), "TimeoutError"),
        (ConnectionError("429 too many requests"), "ConnectionError"),
        ({"events": "not-a-list"}, "ExtractionOutputError"),
    ],
)
def test_failed_row_holds_attempts_and_error_class(db_session, error, error_class):
    _seed_tweet(db_session)
    runtime = _runtime(error, error, error)
    clock = RecordingClock([NOW, NOW + timedelta(seconds=10)])

    outcome = _extract(db_session, runtime, clock)

    row = _row(db_session, outcome.extraction_id)
    assert row.status == "failed"
    assert row.attempts == 3
    assert row.error_class == error_class
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (None, None, None)


def test_error_while_saving_stores_failed_row(db_session, monkeypatch):
    _seed_tweet(db_session)
    runtime = _runtime(ExtractionOutput(events=[]))
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)])
    import app.extraction.service as service

    real_save = service.save_extraction

    def save_failing_once(session, record, events):
        if record.status == "extracted":
            raise RuntimeError("database write failed")
        return real_save(session, record, events)

    monkeypatch.setattr(service, "save_extraction", save_failing_once)

    outcome = _extract(db_session, runtime, clock)

    assert outcome is not None
    assert outcome.status == "failed"
    row = _row(db_session, outcome.extraction_id)
    assert (row.status, row.error_class, row.attempts) == ("failed", "RuntimeError", 1)


def test_error_before_the_model_call_stores_failed_row(db_session):
    _seed_tweet(db_session)

    def broken_spec() -> ChatModelSpec:
        raise ValueError("cannot build the chat model")

    runtime = ExtractionRuntime(
        provider="fake",
        model="fake-model",
        make_spec=broken_spec,
        tracing=None,
        prices={},
        aliases=([], []),
    )
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    outcome = _extract(db_session, runtime, clock)

    row = _row(db_session, outcome.extraction_id)
    assert (row.status, row.error_class, row.attempts) == ("failed", "ValueError", 0)


def test_bad_prices_file_is_a_config_error(monkeypatch, tmp_path):
    bad = tmp_path / "prices.toml"
    bad.write_text("[openai:gpt\n")
    monkeypatch.setattr("app.extraction.service.load_prices", lambda: load_prices(bad))

    with pytest.raises(ConfigError, match="prices.toml or aliases.toml cannot be loaded"):
        load_reference_files()


def test_handler_receives_post_metadata(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(ExtractionOutput(events=[]))
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])
    handler = RecordingHandler()

    _extract(db_session, runtime, clock, handler=handler)

    assert len(handler.chat_model_starts) == 1
    metadata = handler.chat_model_starts[0]["metadata"]
    assert metadata["x_id"] == POST.x_id
    assert metadata["prompt_version"] == PROMPT_VERSION


def test_stored_provider_is_openrouter_and_model_is_answering_id(db_session):
    _seed_tweet(db_session)
    fake = FakeChatModel(responses=[ExtractionOutput(events=[])], response_model="b/fallback")
    spec = ChatModelSpec(provider="openrouter", model="a/primary", chat_model=fake)
    runtime = ExtractionRuntime(
        provider="openrouter",
        model="a/primary",
        make_spec=lambda: spec,
        tracing=None,
        prices={
            "a/primary": Price(input_per_million=1.0, output_per_million=1.0, checked="x"),
            "b/fallback": Price(input_per_million=3.0, output_per_million=7.0, checked="x"),
        },
        aliases=([], []),
        fallback_model="b/fallback",
    )
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    outcome = _extract(db_session, runtime, clock)

    row = _row(db_session, outcome.extraction_id)
    assert (row.provider, row.model) == ("openrouter", "b/fallback")
    assert outcome.cost_usd == (10 / 1_000_000 * 3.0) + (5 / 1_000_000 * 7.0)


def test_failed_row_keeps_the_configured_model(db_session):
    _seed_tweet(db_session)
    runtime = _runtime(RuntimeError("x"), RuntimeError("x"), RuntimeError("x"))
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])

    outcome = _extract(db_session, runtime, clock)

    assert _row(db_session, outcome.extraction_id).model == "fake-model"
