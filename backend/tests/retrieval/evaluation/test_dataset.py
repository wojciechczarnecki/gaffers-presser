import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from app.retrieval.cli import app
from app.retrieval.evaluation.dataset import (
    CorpusPost,
    Judgement,
    Query,
    load_corpus,
    load_queries,
    write_corpus,
    write_queries,
)
from tests.retrieval.helpers import NOW, add_tweet
from tests.retrieval.test_cli import _deps


def test_export_corpus_writes_public_fields_only(db, tmp_path):
    add_tweet(db, 2, "Second post", created_at=NOW - timedelta(hours=1), is_reply=True)
    add_tweet(db, 1, "First post", created_at=NOW - timedelta(hours=2), is_repost=True)
    output = tmp_path / "corpus.jsonl"

    result = CliRunner().invoke(app, ["export-corpus", "--output", str(output)], obj=_deps(db))

    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["x_id"] for row in rows] == [1, 2]
    assert set(rows[0]) == {"x_id", "author_handle", "text", "created_at", "is_repost", "is_reply"}
    assert rows[0]["is_repost"] is True and rows[1]["is_reply"] is True
    assert "exported 2 posts" in result.stdout
    assert [post.x_id for post in load_corpus(output)] == [1, 2]


def test_export_corpus_leaves_out_the_own_test_account(db, tmp_path):
    add_tweet(db, 1, "hello everyone", author="GafferPresser")
    add_tweet(db, 2, "Saka injury", author="FFScout")
    output = tmp_path / "corpus.jsonl"

    result = CliRunner().invoke(app, ["export-corpus", "--output", str(output)], obj=_deps(db))

    assert result.exit_code == 0, result.output
    assert [post.x_id for post in load_corpus(output)] == [2]
    assert "exported 1 posts" in result.stdout


def test_export_corpus_refuses_to_overwrite_without_force(db, tmp_path):
    add_tweet(db, 1)
    output = tmp_path / "corpus.jsonl"
    output.write_text("keep\n")
    result = CliRunner().invoke(app, ["export-corpus", "--output", str(output)], obj=_deps(db))
    assert result.exit_code == 1
    assert output.read_text() == "keep\n"
    forced = CliRunner().invoke(
        app, ["export-corpus", "--output", str(output), "--force"], obj=_deps(db)
    )
    assert forced.exit_code == 0
    assert output.read_text() != "keep\n"


def _query(**fields) -> Query:
    base = {
        "id": "q-post-en-001",
        "text": "saka injury",
        "language": "en",
        "origin": "post",
        "source_x_id": 1,
        "event": None,
        "split": "dev",
        "judgements": [Judgement(x_id=1, relevant=True, reviewed=False, labelled_by="a/model")],
    }
    return Query(**(base | fields))


def test_roundtrip_and_atomic_write(tmp_path):
    corpus = [
        CorpusPost(
            x_id=1,
            author_handle="a",
            text="Saka is out",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            is_repost=False,
            is_reply=False,
        )
    ]
    queries = [
        _query(),
        _query(
            id="q-event-pl-001",
            language="pl",
            origin="event",
            source_x_id=None,
            event={"player": "Saka", "event_type": "out"},
        ),
    ]
    write_corpus(tmp_path / "corpus.jsonl", corpus)
    write_queries(tmp_path / "queries.jsonl", queries)

    assert load_corpus(tmp_path / "corpus.jsonl") == corpus
    assert load_queries(tmp_path / "queries.jsonl") == queries
    assert sorted(path.name for path in tmp_path.iterdir()) == ["corpus.jsonl", "queries.jsonl"]


def test_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "queries.jsonl"
    write_queries(path, [_query()])
    before = path.read_text()

    def boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr("app.retrieval.evaluation.dataset.os.replace", boom)
    with pytest.raises(OSError):
        write_queries(path, [_query(id="q-post-en-002")])
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["queries.jsonl"]


def test_query_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Query.model_validate({**_query().model_dump(), "extra": 1})
    with pytest.raises(ValidationError):
        Judgement.model_validate(
            {"x_id": 1, "relevant": True, "reviewed": False, "labelled_by": "m", "extra": 1}
        )
    with pytest.raises(ValidationError):
        _query(language="de")
