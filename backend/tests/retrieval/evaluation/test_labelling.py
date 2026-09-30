import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.retrieval.evaluation.dataset import (
    CorpusPost,
    Judgement,
    Query,
    load_queries,
    write_queries,
)
from app.retrieval.evaluation.labelling import pool, prelabel
from app.retrieval.evaluation.llm import RelevanceLabel, StructuredCaller
from app.retrieval.evaluation.schema import EVAL_SCHEMA, load_eval_corpus
from app.retrieval.indexing import index_missing
from app.retrieval.search import search
from app.retrieval.tracing import NULL_TRACER
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.fakes import FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import MODEL, PRICES, FixedClock, add_tweet, runtime

WHEN = datetime(2026, 9, 1, tzinfo=UTC)
QUERY_VECTOR = [1.0, 0.0, 0.0]


@pytest.fixture
def eval_db(db):
    yield db
    with db.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {EVAL_SCHEMA} CASCADE"))


def _post(x_id: int, body: str) -> CorpusPost:
    return CorpusPost(
        x_id=x_id,
        author_handle="author",
        text=body,
        created_at=WHEN + timedelta(minutes=x_id),
        is_repost=False,
        is_reply=False,
    )


def _corpus() -> list[CorpusPost]:
    posts = [_post(i, f"Saka injury news number {i}") for i in range(1, 13)]
    posts += [_post(i, f"Unrelated transfer chatter {i}") for i in range(13, 26)]
    return posts


def _embedder(corpus: list[CorpusPost]) -> FakeEmbedder:
    vectors = {"saka injury": QUERY_VECTOR}
    for post in corpus:
        # posts 13.. are the nearest to the query vector, posts 1..12 the farthest
        vectors[post.text] = [1.0, 0.1 * (26 - post.x_id), 0.0]
    return FakeEmbedder(vectors=vectors)


def _query(id_: str = "q-event-en-001", origin="event", source=None, judgements=()) -> Query:
    return Query(
        id=id_,
        text="saka injury",
        language="en",
        origin=origin,
        source_x_id=source,
        event=None,
        split="dev",
        judgements=list(judgements),
    )


def _caller(*labels, model="chat/model") -> tuple[StructuredCaller, FakeChatModel]:
    fake = FakeChatModel(
        responses=[
            value if isinstance(value, BaseException) else RelevanceLabel(relevant=value)
            for value in labels
        ]
    )
    return StructuredCaller(fake, model, {}, FixedClock()), fake


def _ids_of(engine, embedder, mode):
    response = search(engine, "saka injury", mode, embedder=embedder, limit=10, prices=PRICES)
    return [r.x_id for r in response.results]


def test_pools_top10_of_each_mode_and_prelabels_unreviewed(eval_db, tmp_path):
    corpus = _corpus()
    embedder = _embedder(corpus)
    path = tmp_path / "queries.jsonl"
    queries = [_query()]
    write_queries(path, queries)
    caller, fake = _caller(*([True, False] * 30))

    summary = prelabel(
        eval_db, corpus, queries, path, embedder, caller, PRICES, NULL_TRACER, FixedClock()
    )

    eval_engine = load_eval_corpus(eval_db, corpus)
    index_missing(
        eval_engine, runtime(embedder), embedder, NULL_TRACER, FixedClock(), threading.Event()
    )
    expected = []
    for mode in ("fulltext", "vector", "hybrid"):
        for x_id in _ids_of(eval_engine, embedder, mode):
            if x_id not in expected:
                expected.append(x_id)
    assert 10 < len(expected) <= 30
    saved = load_queries(path)
    (query,) = saved
    assert [j.x_id for j in query.judgements] == expected
    assert all(not j.reviewed and j.labelled_by == "chat/model" for j in query.judgements)
    assert [j.relevant for j in query.judgements[:4]] == [True, False, True, False]
    assert summary.queries_labelled == 1
    assert summary.candidates == len(expected)
    assert summary.relevant == sum(j.relevant for j in query.judgements)
    assert summary.failures == 0
    assert len(fake.received_messages) == len(expected)
    assert "saka injury" in fake.received_messages[0][1].content


def test_source_post_joins_the_pool(eval_db):
    corpus = [_post(i, f"Transfer chatter number {i}") for i in range(1, 16)]
    embedder = FakeEmbedder(
        vectors={"saka injury": QUERY_VECTOR}
        | {post.text: [1.0, 0.1 * post.x_id, 0.0] for post in corpus}
    )
    engine = load_eval_corpus(eval_db, corpus)
    index_missing(engine, runtime(embedder), embedder, NULL_TRACER, FixedClock(), threading.Event())

    pooled = pool(engine, _query(origin="post", source=15), embedder, NULL_TRACER, PRICES)
    without_source = pool(engine, _query(origin="post", source=None), embedder, NULL_TRACER, PRICES)
    event_pooled = pool(engine, _query(origin="event", source=15), embedder, NULL_TRACER, PRICES)

    assert sorted(without_source) == list(range(1, 11))
    assert pooled == without_source + [15]
    assert 15 not in event_pooled


def test_prelabel_resumes_and_skips_labelled_queries(eval_db, tmp_path):
    corpus = _corpus()
    embedder = _embedder(corpus)
    path = tmp_path / "queries.jsonl"
    done = Judgement(x_id=1, relevant=True, reviewed=True, labelled_by="owner")
    queries = [_query("q-event-en-001", judgements=[done]), _query("q-event-en-002")]
    write_queries(path, queries)
    caller, fake = _caller(*([True] * 40))

    first = prelabel(
        eval_db, corpus, queries, path, embedder, caller, PRICES, NULL_TRACER, FixedClock()
    )
    saved = load_queries(path)
    assert first.queries_labelled == 1
    assert saved[0].judgements == [done]
    assert saved[1].judgements

    calls = len(fake.received_messages)
    second = prelabel(
        eval_db, corpus, saved, path, embedder, caller, PRICES, NULL_TRACER, FixedClock()
    )
    assert second.queries_labelled == 0
    assert len(fake.received_messages) == calls
    assert load_queries(path) == saved


def test_prelabel_writes_after_every_query(eval_db, tmp_path):
    corpus = _corpus()
    embedder = _embedder(corpus)
    path = tmp_path / "queries.jsonl"
    queries = [_query("q-event-en-001"), _query("q-event-en-002")]
    write_queries(path, queries)
    # 30 labels at most for the first query, then an interrupt in the second one
    caller, _ = _caller(*([True] * 25), KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        prelabel(
            eval_db, corpus, queries, path, embedder, caller, PRICES, NULL_TRACER, FixedClock()
        )
    saved = load_queries(path)
    assert saved[0].judgements
    assert saved[1].judgements == []


def test_failed_label_leaves_the_query_for_a_rerun(eval_db, tmp_path):
    corpus = _corpus()[:12]
    embedder = _embedder(corpus)
    path = tmp_path / "queries.jsonl"
    queries = [_query()]
    write_queries(path, queries)
    fake = FakeChatModel(
        responses=[RuntimeError("down")] * 3 + [RelevanceLabel(relevant=True)] * 40
    )
    caller = StructuredCaller(fake, "chat/model", {}, FixedClock())

    summary = prelabel(
        eval_db, corpus, queries, path, embedder, caller, PRICES, NULL_TRACER, FixedClock()
    )

    assert summary.failures == 1
    assert summary.queries_labelled == 0
    assert load_queries(path)[0].judgements == []

    rerun = prelabel(
        eval_db, corpus, queries, path, embedder, caller, PRICES, NULL_TRACER, FixedClock()
    )

    assert (rerun.failures, rerun.queries_labelled) == (0, 1)
    assert len(load_queries(path)[0].judgements) == rerun.candidates == summary.candidates


def test_prelabel_command_reports_unembeddable_corpus_without_a_traceback(eval_db, tmp_path):
    from typer.testing import CliRunner

    from app.retrieval.cli import RetrievalCliDeps, app
    from app.retrieval.evaluation.dataset import write_corpus
    from tests.retrieval.fakes import AlwaysFailingEmbedder
    from tests.retrieval.test_cli import _deps

    write_corpus(tmp_path / "corpus.jsonl", _corpus()[:2])
    write_queries(tmp_path / "queries.jsonl", [_query()])
    fake = FakeChatModel(responses=[])
    base = _deps(eval_db, {MODEL: AlwaysFailingEmbedder()})
    deps = RetrievalCliDeps(**{**base.__dict__, "make_chat_model": lambda model: fake})

    result = CliRunner().invoke(
        app,
        [
            "prelabel",
            "--queries",
            str(tmp_path / "queries.jsonl"),
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
        ],
        obj=deps,
    )

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "error: 2 corpus posts could not be embedded" in result.stderr


def test_prelabel_never_touches_public_tweet(eval_db, tmp_path):
    add_tweet(eval_db, 900, "Saka injury")
    corpus = _corpus()
    embedder = _embedder(corpus)
    path = tmp_path / "queries.jsonl"
    queries = [_query()]
    write_queries(path, queries)
    caller, _ = _caller(*([True] * 40))
    tracer = RecordingTracer()

    prelabel(eval_db, corpus, queries, path, embedder, caller, PRICES, tracer, FixedClock())

    with eval_db.connect() as conn:
        assert conn.execute(text("SELECT x_id FROM public.tweet")).scalars().all() == [900]
        assert conn.execute(text("SELECT count(*) FROM public.post_embedding")).scalar_one() == 0
    assert 900 not in {j.x_id for j in load_queries(path)[0].judgements}
    assert tracer.searches


def test_prelabel_cost_adds_chat_and_embeddings(eval_db, tmp_path):
    corpus = _corpus()[:12]
    embedder = _embedder(corpus)
    path = tmp_path / "queries.jsonl"
    queries = [_query()]
    write_queries(path, queries)
    fake = FakeChatModel(responses=[RelevanceLabel(relevant=True)] * 40)
    prices = PRICES | {"chat/model": PRICES[MODEL].__class__(1.0, 2.0, "x")}
    caller = StructuredCaller(fake, "chat/model", prices, FixedClock())

    summary = prelabel(
        eval_db, corpus, queries, path, embedder, caller, prices, NULL_TRACER, FixedClock()
    )

    chat = summary.candidates * (10 * 1.0 + 5 * 2.0) / 1e6
    embeddings = (12 * 5 + 2 * 5) * 0.02 / 1e6  # corpus, then the query for two modes
    assert summary.cost_usd == pytest.approx(chat + embeddings)


def test_prelabel_command_prints_the_summary_and_writes_labels(eval_db, tmp_path):
    from typer.testing import CliRunner

    from app.retrieval.cli import RetrievalCliDeps, app
    from app.retrieval.evaluation.dataset import write_corpus
    from tests.retrieval.test_cli import _deps

    corpus = _corpus()[:12]
    write_corpus(tmp_path / "corpus.jsonl", corpus)
    write_queries(tmp_path / "queries.jsonl", [_query()])
    fake = FakeChatModel(responses=[RelevanceLabel(relevant=True)] * 40)
    base = _deps(eval_db, {MODEL: _embedder(corpus)})
    deps = RetrievalCliDeps(**{**base.__dict__, "make_chat_model": lambda model: fake})

    result = CliRunner().invoke(
        app,
        [
            "prelabel",
            "--queries",
            str(tmp_path / "queries.jsonl"),
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
        ],
        obj=deps,
    )

    assert result.exit_code == 0, result.output
    assert "queries labelled: 1" in result.stdout
    assert "candidates: 10" in result.stdout
    assert "relevant: 10" in result.stdout
    assert "label failures: 0" in result.stdout
    assert "total cost: $" in result.stdout
    assert len(load_queries(tmp_path / "queries.jsonl")[0].judgements) == 10
