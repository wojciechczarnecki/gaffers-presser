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


def test_status_prints_the_latest_embedding_with_its_latency(db):
    from app.retrieval.store import save_embedded

    add_tweet(db, 1)
    add_tweet(db, 2)
    with Session(db) as session, session.begin():
        for x_id, when in ((1, NOW - timedelta(minutes=5)), (2, NOW)):
            save_embedded(
                session,
                x_id=x_id,
                model=MODEL,
                vector=[1.0, 0.0, 0.0],
                input_tokens=5,
                cost_usd=1e-7,
                latency_seconds=12.5 * x_id,
                attempts=1,
                now=when,
            )

    result = _run(_deps(db), "status")

    assert "latest embedding: 2026-09-30T12:00:00Z x_id=2 model=fake/embed latency=25.0s" in (
        result.stdout.splitlines()
    )


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


def _seed_search(db):
    add_tweet(db, 1, "Saka injury doubt", author="fabrizio", created_at=NOW - timedelta(hours=3))
    add_tweet(db, 2, "Saka scores again", created_at=NOW - timedelta(hours=2))
    add_tweet(db, 3, "Haaland hat-trick", created_at=NOW - timedelta(hours=1))
    _run(_deps(db, {MODEL: FakeEmbedder(default=[1.0, 0.0, 0.0])}), "index")


def test_search_prints_ranked_results_with_ranks(db):
    _seed_search(db)
    embedder = FakeEmbedder(default=[1.0, 0.0, 0.0])
    result = _run(_deps(db, {MODEL: embedder}), "search", "saka injury")
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("mode: hybrid  model: fake/embed")
    first = next(line for line in lines if line.startswith("1. "))
    assert re.fullmatch(
        r"1\. \d\.\d{4}  fts=\d+  vec=\d+  @\w+  \d{4}-\d\d-\d\d \d\d:\d\d  \d+", first
    )
    assert "    Saka injury doubt" in lines or "    Haaland hat-trick" in lines
    assert any("fts=1" in line and "@fabrizio" in line for line in lines)


def test_search_fulltext_shows_no_vector_rank(db):
    _seed_search(db)
    result = _run(_deps(db), "search", "haaland", "--mode", "fulltext")
    assert "fts=1  vec=-" in result.stdout
    assert result.stdout.splitlines()[0].startswith("mode: fulltext  model: -")


def test_search_time_filters_in_warsaw_compared_in_utc(db):
    # 22:30 UTC on 2026-09-30 is 00:30 on 2026-10-01 in Warsaw (UTC+2)
    from datetime import UTC, datetime

    add_tweet(db, 1, "Saka injury", created_at=datetime(2026, 9, 30, 22, 30, tzinfo=UTC))
    kept = _run(_deps(db), "search", "saka", "--mode", "fulltext", "--since", "2026-10-01")
    dropped = _run(_deps(db), "search", "saka", "--mode", "fulltext", "--until", "2026-10-01")
    assert "2026-10-01 00:30" in kept.stdout
    assert "no results" in dropped.stdout
    assert "since 2026-10-01 00:00" in kept.stdout
    explicit = _run(
        _deps(db), "search", "saka", "--mode", "fulltext", "--since", "2026-09-30T22:31+00:00"
    )
    assert "no results" in explicit.stdout


def test_search_rejects_a_malformed_time(db):
    result = _run(_deps(db), "search", "saka", "--since", "yesterday")
    assert result.exit_code == 1
    assert "--since" in result.stderr


def test_search_traced_with_query_mode_and_ids(db):
    _seed_search(db)
    tracer = RecordingTracer()
    deps = _deps(db, {MODEL: FakeEmbedder(default=[1.0, 0.0, 0.0])}, tracer=tracer)
    _run(deps, "search", "saka", "--mode", "hybrid")
    (record,) = tracer.searches
    assert (record["query"], record["mode"]) == ("saka", "hybrid")
    assert set(record["ids_by_mode"]) == {"fulltext", "vector", "hybrid"}
    assert set(record["ids_by_mode"]["fulltext"]) == {1, 2}
    assert tracer.flushed == 1


def test_vector_search_without_embeddings_fails_with_hint(db):
    add_tweet(db, 1)
    result = _run(_deps(db), "search", "saka", "--mode", "vector")
    assert result.exit_code == 1
    assert "no embeddings for model fake/embed" in result.stderr
    assert "app.retrieval index" in result.stderr


def test_fulltext_search_needs_no_key(db):
    add_tweet(db, 1, "Saka injury")

    def no_embedder(model):
        raise ConfigError("OPENROUTER_API_KEY is not set")

    deps = RetrievalCliDeps(**{**_deps(db).__dict__, "make_embedder": no_embedder})
    ok = _run(deps, "search", "saka", "--mode", "fulltext")
    assert ok.exit_code == 0, ok.output
    assert "fts=1" in ok.stdout
    hybrid = _run(deps, "search", "saka", "--mode", "hybrid")
    assert hybrid.exit_code == 1
    assert "OPENROUTER_API_KEY" in hybrid.stderr
    assert "--mode fulltext" in hybrid.stderr


def test_search_shows_a_failed_vector_leg(db):
    _seed_search(db)
    from tests.retrieval.fakes import AlwaysFailingEmbedder

    deps = _deps(db, {MODEL: AlwaysFailingEmbedder()})
    result = _run(deps, "search", "saka")
    assert result.exit_code == 0
    expected = "vector leg failed (query embedding failed: RuntimeError) — full-text only"
    assert expected in result.stdout


def _review_files(tmp_path, split="dev"):
    from datetime import UTC, datetime

    from app.retrieval.evaluation.dataset import (
        CorpusPost,
        Judgement,
        Query,
        write_corpus,
        write_queries,
    )

    corpus = [
        CorpusPost(
            x_id=i,
            author_handle=f"author{i}",
            text=f"Post text {i}",
            created_at=datetime(2026, 9, 30, 22, 30, tzinfo=UTC),
            is_repost=False,
            is_reply=False,
        )
        for i in range(1, 7)
    ]

    def judgement(x_id):
        return Judgement(x_id=x_id, relevant=True, reviewed=False, labelled_by="chat/model")

    def query(id_, x_ids, split):
        return Query(
            id=id_,
            text=f"text of {id_}",
            language="en",
            origin="post",
            source_x_id=None,
            event=None,
            split=split,
            judgements=[judgement(x) for x in x_ids],
        )

    queries = [query("q-post-en-001", [1, 2, 3], split), query("q-post-en-002", [4], "test")]
    write_corpus(tmp_path / "corpus.jsonl", corpus)
    write_queries(tmp_path / "queries.jsonl", queries)
    return [
        "--queries",
        str(tmp_path / "queries.jsonl"),
        "--corpus",
        str(tmp_path / "corpus.jsonl"),
    ]


def _review(args, keys):
    return CliRunner().invoke(app, ["review", *args], input="".join(f"{k}\n" for k in keys))


def test_review_accept_flip_skip_add_and_saves_after_each(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries

    args = _review_files(tmp_path)
    path = tmp_path / "queries.jsonl"

    result = _review(args, ["a", "f", "+", "5", "s", "q"])

    assert result.exit_code == 0, result.output
    first = load_queries(path)[0].judgements
    by_id = {j.x_id: j for j in first}
    assert (by_id[1].reviewed, by_id[1].relevant) == (True, True)
    assert (by_id[2].reviewed, by_id[2].relevant) == (True, False)
    assert (by_id[3].reviewed, by_id[3].relevant) == (False, True)
    assert (by_id[5].reviewed, by_id[5].relevant, by_id[5].labelled_by) == (True, True, "owner")
    assert not load_queries(path)[1].judgements[0].reviewed
    assert "accepted: 1  flipped: 1  skipped: 1  added: 1" in result.stdout
    assert "2026-10-01 00:30" in result.stdout
    assert "chat/model says: relevant" in result.stdout


def test_review_saves_before_an_interrupt(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries

    args = _review_files(tmp_path)
    result = _review(args, ["a"])  # the input ends: an abort after the first decision
    assert result.exit_code == 130
    assert load_queries(tmp_path / "queries.jsonl")[0].judgements[0].reviewed
    assert "reviewed: 1/4" in result.stdout


def test_review_next_query_moves_on(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries

    args = _review_files(tmp_path)
    result = _review(args, ["n", "a", "q"])
    assert result.exit_code == 0
    queries = load_queries(tmp_path / "queries.jsonl")
    assert not any(j.reviewed for j in queries[0].judgements)
    assert queries[1].judgements[0].reviewed


def test_review_split_filter(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries

    args = _review_files(tmp_path)
    result = _review([*args, "--split", "test"], ["a", "q"])
    assert result.exit_code == 0
    queries = load_queries(tmp_path / "queries.jsonl")
    assert queries[1].judgements[0].reviewed
    assert not queries[0].judgements[0].reviewed


def test_review_add_rejects_unknown_id(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries

    args = _review_files(tmp_path)
    before = (tmp_path / "queries.jsonl").read_text()
    result = _review(args, ["+", "999", "q"])
    assert result.exit_code == 0
    assert "'999' is not in the corpus" in result.stdout
    assert (tmp_path / "queries.jsonl").read_text() == before
    assert len(load_queries(tmp_path / "queries.jsonl")[0].judgements) == 3


def test_review_add_replaces_an_existing_judgement_of_that_post(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries

    args = _review_files(tmp_path)
    result = _review(args, ["+", "2", "a", "a", "n", "q"])
    assert result.exit_code == 0
    judgements = load_queries(tmp_path / "queries.jsonl")[0].judgements
    assert sorted(j.x_id for j in judgements) == [1, 2, 3]
    assert next(j for j in judgements if j.x_id == 2).labelled_by == "owner"


def test_review_nothing_to_review(tmp_path):
    from app.retrieval.evaluation.dataset import load_queries, write_queries

    args = _review_files(tmp_path)
    queries = [
        q.model_copy(
            update={"judgements": [j.model_copy(update={"reviewed": True}) for j in q.judgements]}
        )
        for q in load_queries(tmp_path / "queries.jsonl")
    ]
    write_queries(tmp_path / "queries.jsonl", queries)
    result = _review(args, [])
    assert result.exit_code == 0
    assert "nothing to review" in result.stdout


def test_review_needs_no_database_or_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = _review_files(tmp_path)
    result = CliRunner().invoke(app, ["review", *args], input="q\n")
    assert result.exit_code == 0


def test_hybrid_search_without_embeddings_shows_fulltext_and_the_hint(db):
    add_tweet(db, 1, "Saka injury")
    result = _run(_deps(db), "search", "saka")
    assert result.exit_code == 0, result.output
    assert (
        "vector leg failed (no embeddings for model fake/embed;"
        " run python -m app.retrieval index --model fake/embed) — full-text only"
    ) in result.stdout
    assert any(
        line.startswith("1. ") and "fts=1  vec=-" in line for line in result.stdout.splitlines()
    )
