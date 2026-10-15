import json
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlmodel import Session
from typer.testing import CliRunner

from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import export_corpus, load_corpus, load_manifest
from app.tweets.models import ListMembership
from tests.retrieval.helpers import add_tweet
from tests.retrieval.test_cli import _deps


def at(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def seed(db):
    add_tweet(db, 1, "early", created_at=at(1), author="Listed")
    add_tweet(db, 2, "middle", created_at=at(5), author="other")
    add_tweet(db, 3, "late", created_at=at(9), author="listed")
    add_tweet(db, 4, "own account", created_at=at(5), author="GafferPresser")
    add_tweet(db, 5, "quote of two", created_at=at(6), author="listed", quoted_x_id=2)
    with Session(db) as session:
        session.add(ListMembership(list_id=1, source="x", fetched_at=at(2), handles=["old"]))
        session.add(ListMembership(list_id=1, source="x", fetched_at=at(3), handles=["listed"]))
        session.commit()


def test_export_bounds_by_since_and_until_and_skips_the_test_account(db):
    seed(db)
    everything = export_corpus(db)
    assert [p.x_id for p in everything.posts] == [1, 2, 5, 3]
    assert everything.list_handles == ["listed"]
    assert {p.x_id: p.is_source for p in everything.posts} == {1: True, 2: True, 5: True, 3: True}

    bounded = export_corpus(db, since=at(5), until=at(9))
    assert [p.x_id for p in bounded.posts] == [2, 5]
    only_early = export_corpus(db, until=at(5))
    assert [p.x_id for p in only_early.posts] == [1]
    assert 4 not in {p.x_id for p in everything.posts}


def test_export_flags_sources_over_the_exported_posts(db):
    seed(db)
    # the quote (5) is outside the window: post 2 is then no source
    bounded = export_corpus(db, since=at(4), until=at(6))
    assert {p.x_id: p.is_source for p in bounded.posts} == {2: False}
    assert export_corpus(db, since=at(4), until=at(7)).posts[0].is_source is True


def test_export_runs_on_a_read_only_connection(db):
    seed(db)
    read_only = create_engine(
        db.url, connect_args={"options": "-c default_transaction_read_only=on"}
    )
    try:
        assert len(export_corpus(read_only).posts) == 4
    finally:
        read_only.dispose()


def test_a_null_snapshot_makes_every_post_a_source(db):
    add_tweet(db, 1, "x", author="anyone")
    assert export_corpus(db).list_handles is None
    assert export_corpus(db).posts[0].is_source is True


def test_the_cli_writes_corpus_and_set_json_and_refuses_an_existing_corpus(db, tmp_path):
    seed(db)
    args = ["export-corpus", "--set", str(tmp_path), "--until", "2026-10-09"]
    result = CliRunner().invoke(app, args, obj=_deps(db))
    assert result.exit_code == 0, result.output
    assert [p.x_id for p in load_corpus(tmp_path / "corpus.jsonl")] == [1, 2, 5]
    manifest = load_manifest(tmp_path)
    assert manifest.version == "v2"
    assert manifest.list_handles == ["listed"]
    assert manifest.corpus_until == datetime(2026, 10, 8, 22, 0, tzinfo=UTC)
    assert manifest.corpus_since is None
    raw = json.loads((tmp_path / "set.json").read_text())
    assert set(raw) == {"version", "list_handles", "corpus_since", "corpus_until", "exported_at"}

    again = CliRunner().invoke(app, args, obj=_deps(db))
    assert again.exit_code == 1
    assert "--force" in again.stderr
    assert CliRunner().invoke(app, [*args, "--force"], obj=_deps(db)).exit_code == 0
