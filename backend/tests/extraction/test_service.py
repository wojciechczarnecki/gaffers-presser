import threading
from datetime import UTC, datetime, timedelta

from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import ExtractedEvent, ExtractionOutput, PostInput
from app.extraction.service import RETRY_BACKOFF_SECONDS, ExtractionRuntime, extract_post
from app.extraction.store import current_extraction
from app.tweets.models import Tweet
from tests.extraction.fakes import FakeChatModel

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

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


def _runtime(*responses) -> ExtractionRuntime:
    fake = FakeChatModel(responses=list(responses))
    spec = ChatModelSpec(provider="fake", model="fake-model", chat_model=fake)
    return ExtractionRuntime(
        provider="fake", model="fake-model", make_spec=lambda: spec, tracing=None
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


def test_cost_null_without_price(db_session, monkeypatch):
    _seed_tweet(db_session)
    output = ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="out", certainty="confirmed")
        ]
    )
    runtime = _runtime(output)
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])
    monkeypatch.setattr("app.extraction.service.load_prices", lambda: {})

    outcome = extract_post(
        db_session.get_bind(), runtime, POST, clock, threading.Event(), None, record_latency=False
    )

    assert outcome is not None
    assert outcome.cost_usd is None


def test_cost_from_price_table(db_session, monkeypatch):
    _seed_tweet(db_session)
    output = ExtractionOutput(events=[])
    runtime = _runtime(output)
    clock = RecordingClock([NOW, NOW + timedelta(seconds=1)])
    from app.extraction.pricing import Price

    monkeypatch.setattr(
        "app.extraction.service.load_prices",
        lambda: {
            "fake:fake-model": Price(
                input_per_million=1.0, output_per_million=2.0, checked="2026-09-28"
            )
        },
    )

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
    from sqlmodel import Session, select

    from app.extraction.models import Extraction

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
    from sqlmodel import Session, select

    from app.extraction.models import Extraction

    with Session(db_session.get_bind()) as session:
        row = session.exec(select(Extraction).where(Extraction.id == outcome.extraction_id)).one()
        assert sentinel not in (row.error_class or "")
