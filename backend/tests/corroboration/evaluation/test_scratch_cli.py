from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlmodel import Session
from typer.testing import CliRunner

from app.corroboration.config import CorroborationSettings
from app.corroboration.evaluation import cli
from app.corroboration.evaluation.cli import app
from app.corroboration.evaluation.scratch import ExtractSummary, ScratchSummary
from app.tweets.models import Tweet
from tests.corroboration.evaluation.test_runner import _deps
from tests.retrieval.fakes import FakeEmbedder

CONFIGURED = "postgresql+psycopg://presser:secret@localhost:5433/presser"
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def configured(monkeypatch):
    # The commands compare against the application's configured database; tests never read it.
    monkeypatch.setattr(cli, "load_settings", lambda: SimpleNamespace(database_url=CONFIGURED))


def _invoke(command, url, *args, deps=None):
    return CliRunner().invoke(app, [command, "--database-url", url, *args], obj=deps)


@pytest.mark.parametrize("command", ["build-scratch", "build-candidates"])
def test_scratch_commands_refuse_the_compose_port(configured, command, tmp_path):
    extra = ["--out", str(tmp_path / "out")] if command == "build-candidates" else []
    result = _invoke(command, "postgresql+psycopg://u:p@localhost:5432/eval", *extra)
    assert result.exit_code == 1
    assert "5432" in result.output


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
@pytest.mark.parametrize("command", ["build-scratch", "build-candidates"])
def test_scratch_commands_refuse_the_configured_database(configured, command, host, tmp_path):
    extra = ["--out", str(tmp_path / "out")] if command == "build-candidates" else []
    result = _invoke(command, f"postgresql+psycopg://other:pw@{host}:5433/presser", *extra)
    assert result.exit_code == 1
    assert "is the configured" in result.output


def test_build_candidates_refuses_a_database_with_live_posts(configured, db, tmp_path):
    with Session(db) as session:
        session.add(
            Tweet(
                x_id=1,
                author_handle="x",
                text="t",
                created_at=T0,
                first_fetched_at=T0,
                source="list",
                is_repost=False,
                is_reply=False,
                raw={},
            )
        )
        session.commit()
    url = db.url.render_as_string(hide_password=False)
    result = _invoke("build-candidates", url, "--out", str(tmp_path / "out"))
    assert result.exit_code == 1
    assert "other posts than the evaluation corpus" in result.output
    assert not (tmp_path / "out").exists()


def test_build_scratch_exits_1_when_the_cap_stops_the_extraction(configured, monkeypatch):
    summary = ScratchSummary(
        seeded=True,
        embedded=2,
        windows=[],
        extract=ExtractSummary(extracted=3, failed=0, stopped_by_cap=True),
        embeddings_cost_usd=0.01,
        extraction_cost_usd=0.5,
    )
    monkeypatch.setattr(cli, "run_build_scratch", lambda *a, **k: summary)
    monkeypatch.setattr(cli, "build_embedder", lambda config: FakeEmbedder())
    monkeypatch.setattr(cli, "load_corpus", lambda path: [])
    settings = CorroborationSettings(_env_file=None, openrouter_api_key=SecretStr("test-key"))
    result = _invoke(
        "build-scratch",
        "postgresql+psycopg://eval:eval@localhost:55433/eval",
        "--max-cost-usd",
        "0.5",
        deps=_deps(settings=settings),
    )
    assert result.exit_code == 1
    assert "extracted: 3" in result.stdout
    assert "cost cap of $0.50 stopped the extraction" in result.output
