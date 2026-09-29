import logging
import os
import re
from datetime import timedelta

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.core.errors import ConfigError
from app.retrieval.cli import RetrievalCliDeps, app
from app.retrieval.config import RetrievalSettings
from app.retrieval.models import PostEmbedding
from app.retrieval.tracing import make_tracer
from tests.retrieval.fakes import FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import MODEL, NOW, PRICES, FixedClock, add_tweet

VARIABLES = re.compile(r"(LLM_.*|LANGFUSE_.*|.*_API_KEY|USD_PLN_RATE|EMBEDDING_MODEL)")


@pytest.fixture(autouse=True)
def _no_provider_variables(monkeypatch):
    for name in list(os.environ):
        if VARIABLES.fullmatch(name):
            monkeypatch.delenv(name)


def _deps(db, embedders=None, tracer=None, **extra) -> RetrievalCliDeps:
    embedders = embedders or {}

    def make_embedder(model):
        chosen = model or MODEL
        return embedders.setdefault(chosen, FakeEmbedder(model=chosen))

    return RetrievalCliDeps(
        engine=db,
        settings=RetrievalSettings(_env_file=None),
        make_embedder=make_embedder,
        clock=FixedClock(NOW),
        make_tracer=lambda: tracer or RecordingTracer(),
        prices=PRICES | {"other/embed": PRICES[MODEL]},
        **extra,
    )


def _run(deps, *args):
    return CliRunner().invoke(app, list(args), obj=deps)


def _rows(db) -> list[PostEmbedding]:
    with Session(db) as session:
        return list(session.exec(select(PostEmbedding).order_by(PostEmbedding.id)))


def test_index_embeds_missing_and_prints_counts_and_cost(db):
    for x_id in (1, 2, 3):
        add_tweet(db, x_id, f"post {x_id}")
    result = _run(_deps(db), "index")
    assert result.exit_code == 0, result.output
    assert "embedded: 3" in result.stdout
    assert "failed: 0" in result.stdout
    assert "total cost: $0.000000" in result.stdout  # 15 tokens x $0.02 / 1e6 = 3e-7
    assert len(_rows(db)) == 3


def test_index_second_run_embeds_nothing(db):
    add_tweet(db, 1)
    deps = _deps(db)
    _run(deps, "index")
    result = _run(deps, "index")
    assert "embedded: 0" in result.stdout
    assert "total cost: n/a" in result.stdout
    assert len(_rows(db)) == 1


def test_index_model_option_leaves_other_model_untouched(db):
    add_tweet(db, 1)
    deps = _deps(db)
    _run(deps, "index")
    before = [(r.model, list(r.embedding)) for r in _rows(db)]

    result = _run(deps, "index", "--model", "other/embed")

    assert "embedded: 1" in result.stdout
    rows = _rows(db)
    assert sorted(r.model for r in rows) == [MODEL, "other/embed"]
    assert [(r.model, list(r.embedding)) for r in rows if r.model == MODEL] == before


def test_index_counts_failed_posts(db):
    add_tweet(db, 1)
    embedder = FakeEmbedder(responses=[RuntimeError("x")] * 3)
    result = _run(_deps(db, {MODEL: embedder}), "index")
    assert "embedded: 0" in result.stdout
    assert "failed: 1" in result.stdout
    assert _rows(db)[0].error_class == "RuntimeError"


def test_status_prints_counts_latest_and_cost(db):
    for x_id in (1, 2, 3):
        add_tweet(db, x_id, f"post {x_id}", first_fetched_at=NOW - timedelta(seconds=9))
    deps = _deps(
        db, {MODEL: FakeEmbedder(responses=[None, RuntimeError("x")] + [RuntimeError("x")] * 2)}
    )
    _run(deps, "index")
    _run(_deps(db), "index", "--model", "other/embed")
    add_tweet(db, 4, "post 4")

    result = _run(_deps(db), "status")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "posts: 4"
    assert f"model {MODEL}: embedded 2  missing 1  failed 1" in lines
    assert "model other/embed: embedded 3  missing 1  failed 0" in lines
    assert any(line.startswith("latest embedding: ") and "x_id=" in line for line in lines)
    assert lines[-1].startswith("total embedding cost: 0.0000")


def test_status_on_empty_database(db):
    result = _run(_deps(db), "status")
    lines = result.stdout.splitlines()
    assert lines[0] == "posts: 0"
    assert "model openai/text-embedding-3-small: embedded 0  missing 0  failed 0" in lines
    assert "latest embedding: never" in lines


def test_index_without_langfuse_logs_once(db, caplog):
    for x_id in (1, 2, 3):
        add_tweet(db, x_id, f"post {x_id}")
    deps = _deps(db)
    deps = RetrievalCliDeps(**{**deps.__dict__, "make_tracer": lambda: make_tracer(None)})
    with caplog.at_level(logging.WARNING):
        result = _run(deps, "index")
    assert result.exit_code == 0
    warnings = [r for r in caplog.records if "retrieval tracing disabled" in r.getMessage()]
    assert len(warnings) == 1


def test_index_without_key_fails_naming_the_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["index"])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.stderr


def test_index_config_error_names_the_variable(db):
    def broken(model):
        raise ConfigError("EMBEDDING_MODEL names a model with no row in prices.toml")

    deps = RetrievalCliDeps(**{**_deps(db).__dict__, "make_embedder": broken})
    result = _run(deps, "index")
    assert result.exit_code == 1
    assert "EMBEDDING_MODEL" in result.stderr
