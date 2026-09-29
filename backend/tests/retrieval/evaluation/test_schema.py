import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.retrieval.evaluation.dataset import CorpusPost
from app.retrieval.evaluation.schema import EVAL_SCHEMA, load_eval_corpus
from app.retrieval.indexing import index_missing
from app.retrieval.search import search
from app.retrieval.tracing import NULL_TRACER
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import FixedClock, add_tweet, runtime

WHEN = datetime(2026, 9, 1, tzinfo=UTC)


def _post(x_id: int, body: str) -> CorpusPost:
    return CorpusPost(
        x_id=x_id,
        author_handle="author",
        text=body,
        created_at=WHEN,
        is_repost=False,
        is_reply=False,
    )


@pytest.fixture
def eval_db(db):
    yield db
    with db.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {EVAL_SCHEMA} CASCADE"))


def _count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_eval_schema_never_touches_public_tweet(eval_db):
    add_tweet(eval_db, 900, "Saka injury")
    corpus = [_post(1, "Saka injury doubt"), _post(2, "Haaland injury"), _post(3, "no news")]

    eval_engine = load_eval_corpus(eval_db, corpus)

    response = search(eval_engine, "injury", "fulltext")
    assert sorted(r.x_id for r in response.results) == [1, 2]

    embedder = FakeEmbedder()
    summary = index_missing(
        eval_engine, runtime(embedder), embedder, NULL_TRACER, FixedClock(), threading.Event()
    )
    assert summary.embedded == 3
    assert _count(eval_db, f"{EVAL_SCHEMA}.post_embedding") == 3
    assert _count(eval_db, f"{EVAL_SCHEMA}.tweet") == 3
    with eval_db.connect() as conn:
        public_ids = conn.execute(text("SELECT x_id FROM public.tweet")).scalars().all()
    assert public_ids == [900]
    assert _count(eval_db, "public.post_embedding") == 0

    vector = search(eval_engine, "injury", "vector", embedder=embedder)
    assert sorted(r.x_id for r in vector.results) == [1, 2, 3]


def test_reload_replaces_previous_corpus(eval_db):
    load_eval_corpus(eval_db, [_post(1, "first corpus"), _post(2, "first corpus")])
    engine = load_eval_corpus(eval_db, [_post(7, "second corpus")])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT x_id FROM tweet")).scalars().all() == [7]
        assert conn.execute(text("SELECT count(*) FROM post_embedding")).scalar_one() == 0


def test_corpus_flags_and_times_are_stored(eval_db):
    post = _post(5, "A reply").model_copy(update={"is_reply": True, "is_repost": True})
    engine = load_eval_corpus(eval_db, [post])
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT is_reply, is_repost, created_at, first_fetched_at, source FROM tweet")
        ).one()
    assert row.is_reply and row.is_repost
    assert row.created_at == WHEN == row.first_fetched_at
    assert row.source == "eval-corpus"
