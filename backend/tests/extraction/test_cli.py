import subprocess
import sys
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select
from typer.testing import CliRunner

from app.core.settings import ExtractionSettings
from app.extraction.cli import ExtractionCliDeps, app, build_spec_from_settings
from app.extraction.models import Extraction
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import ExtractedEvent, ExtractionOutput
from app.extraction.store import ExtractionRecord, save_extraction
from app.tweets.models import Tweet
from tests.conftest import BACKEND_DIR
from tests.extraction.fakes import FakeChatModel

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        pass


def _tweet(x_id: int, created_at: datetime = NOW) -> Tweet:
    return Tweet(
        x_id=x_id,
        author_handle="reporter",
        text=f"Post {x_id}",
        created_at=created_at,
        first_fetched_at=created_at,
        source="list",
        is_repost=False,
        is_reply=False,
        raw={},
    )


def _build_spec(*responses, provider: str = "fake", model: str = "fake-model"):
    fake = FakeChatModel(responses=list(responses))
    spec = ChatModelSpec(provider=provider, model=model, chat_model=fake)

    def build_spec(cli_provider, cli_model):
        return ChatModelSpec(
            provider=cli_provider or provider,
            model=cli_model or model,
            chat_model=fake,
        )

    return build_spec, spec


def _deps(db, build_spec, settings=None, clock=None) -> ExtractionCliDeps:
    return ExtractionCliDeps(
        engine=db,
        settings=settings or ExtractionSettings(),
        build_spec=build_spec,
        clock=clock or FixedClock(NOW),
    )


def test_reextract_by_x_id(db):
    with Session(db) as session:
        session.add(_tweet(1))
        session.commit()
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=1,
                status="extracted",
                provider="old",
                model="old-model",
                prompt_version="v0",
                started_at=NOW,
                finished_at=NOW,
                attempts=1,
            ),
            [],
        )

    build_spec, _ = _build_spec(ExtractionOutput(events=[]))
    result = CliRunner().invoke(app, ["reextract", "--x-id", "1"], obj=_deps(db, build_spec))

    assert result.exit_code == 0
    assert "posts processed: 1" in result.stdout
    assert "events: 0" in result.stdout
    assert "failures: 0" in result.stdout
    assert "total cost: n/a" in result.stdout

    with Session(db) as session:
        rows = session.exec(select(Extraction).where(Extraction.tweet_x_id == 1)).all()
    assert len(rows) == 2
    assert {r.provider for r in rows} == {"old", "fake"}


def test_reextract_range(db):
    with Session(db) as session:
        session.add(_tweet(1, created_at=NOW))
        session.add(_tweet(2, created_at=NOW + timedelta(hours=1)))
        session.add(_tweet(3, created_at=NOW + timedelta(hours=2)))
        session.commit()

    build_spec, _ = _build_spec(ExtractionOutput(events=[]), ExtractionOutput(events=[]))
    result = CliRunner().invoke(
        app,
        [
            "reextract",
            "--since",
            NOW.isoformat(),
            "--until",
            (NOW + timedelta(hours=1)).isoformat(),
        ],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    assert "posts processed: 2" in result.stdout
    with Session(db) as session:
        rows = session.exec(select(Extraction)).all()
    assert {r.tweet_x_id for r in rows} == {1, 2}


def test_reextract_failed_only(db):
    with Session(db) as session:
        session.add(_tweet(1))
        session.add(_tweet(2))
        session.commit()
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=1,
                status="failed",
                provider="old",
                model="old-model",
                prompt_version="v0",
                started_at=NOW,
                finished_at=NOW,
                attempts=3,
                error_class="RuntimeError",
            ),
            [],
        )
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=2,
                status="extracted",
                provider="old",
                model="old-model",
                prompt_version="v0",
                started_at=NOW,
                finished_at=NOW,
                attempts=1,
            ),
            [],
        )

    build_spec, _ = _build_spec(ExtractionOutput(events=[]))
    result = CliRunner().invoke(app, ["reextract", "--failed"], obj=_deps(db, build_spec))

    assert result.exit_code == 0
    assert "posts processed: 1" in result.stdout
    with Session(db) as session:
        rows = session.exec(
            select(Extraction).where(Extraction.tweet_x_id == 1, Extraction.status == "extracted")
        ).all()
    assert len(rows) == 1


def test_reextract_with_other_model(db):
    with Session(db) as session:
        session.add(_tweet(1))
        session.commit()

    build_spec, _ = _build_spec(ExtractionOutput(events=[]), provider="fake", model="fake-model")
    result = CliRunner().invoke(
        app,
        ["reextract", "--x-id", "1", "--provider", "other", "--model", "other-model"],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    with Session(db) as session:
        row = session.exec(select(Extraction).where(Extraction.tweet_x_id == 1)).one()
    assert row.provider == "other"
    assert row.model == "other-model"


def test_reextract_events_and_failures_counted(db):
    with Session(db) as session:
        session.add(_tweet(1, created_at=NOW))
        session.add(_tweet(2, created_at=NOW + timedelta(hours=1)))
        session.commit()

    build_spec, _ = _build_spec(
        ExtractionOutput(
            events=[
                ExtractedEvent(player="Haaland", team=None, event_type="out", certainty="confirmed")
            ]
        ),
        RuntimeError("boom"),
        RuntimeError("boom"),
        RuntimeError("boom"),
    )
    result = CliRunner().invoke(
        app,
        [
            "reextract",
            "--since",
            NOW.isoformat(),
            "--until",
            (NOW + timedelta(hours=1)).isoformat(),
        ],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    assert "posts processed: 2" in result.stdout
    assert "events: 1" in result.stdout
    assert "failures: 1" in result.stdout


def test_reextract_requires_one_selector(db):
    build_spec, _ = _build_spec()
    result = CliRunner().invoke(app, ["reextract"], obj=_deps(db, build_spec))
    assert result.exit_code == 1
    assert "exactly one" in result.stderr

    result = CliRunner().invoke(
        app, ["reextract", "--x-id", "1", "--failed"], obj=_deps(db, build_spec)
    )
    assert result.exit_code == 1
    assert "exactly one" in result.stderr

    result = CliRunner().invoke(
        app, ["reextract", "--since", NOW.isoformat()], obj=_deps(db, build_spec)
    )
    assert result.exit_code == 1


def test_reextract_config_error_names_variable(db, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ExtractionSettings(llm_provider="", llm_model="")
    build_spec = build_spec_from_settings(settings)
    result = CliRunner().invoke(
        app,
        ["reextract", "--x-id", "1", "--provider", "openai"],
        obj=_deps(db, build_spec, settings=settings),
    )
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.stderr


def test_help():
    result = subprocess.run(
        [sys.executable, "-m", "app.extraction", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0
    assert "reextract" in result.stdout

    result = subprocess.run(
        [sys.executable, "-m", "app.extraction", "reextract", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0
