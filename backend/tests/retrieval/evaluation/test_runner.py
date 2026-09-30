import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from app.retrieval.cli import app
from app.retrieval.evaluation.dataset import (
    CorpusPost,
    Judgement,
    Query,
    write_corpus,
    write_queries,
)
from app.retrieval.evaluation.runner import (
    EvaluationError,
    default_run_name,
    format_table,
    run_evaluation,
)
from app.retrieval.evaluation.schema import EVAL_SCHEMA
from tests.retrieval.fakes import AlwaysFailingEmbedder, FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import MODEL, PRICES, FixedClock
from tests.retrieval.test_cli import _deps

WHEN = datetime(2026, 9, 1, tzinfo=UTC)


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


CORPUS = [
    _post(1, "Saka injury doubt"),
    _post(2, "Haaland scores hat-trick"),
    _post(3, "Odegaard back in training"),
    _post(4, "Kontuzja Sakę wyklucza"),
]
EN = "saka injury"
PL = "kontuzja saka"


def _embedder() -> FakeEmbedder:
    vectors = {
        EN: [1.0, 0.0, 0.0],
        PL: [0.0, 0.0, 1.0],
        CORPUS[0].text: [1.0, 0.0, 0.0],
        CORPUS[1].text: [0.0, 1.0, 0.0],
        CORPUS[2].text: [0.0, 0.0, 1.0],
        CORPUS[3].text: [0.5, 0.5, 0.0],
    }
    return FakeEmbedder(vectors=vectors)


def _judgement(x_id: int, reviewed: bool, relevant: bool = True) -> Judgement:
    return Judgement(x_id=x_id, relevant=relevant, reviewed=reviewed, labelled_by="chat/model")


def _queries(split="dev") -> list[Query]:
    return [
        Query(
            id="q-post-en-001",
            text=EN,
            language="en",
            origin="post",
            source_x_id=1,
            event=None,
            split=split,
            judgements=[_judgement(1, True), _judgement(3, False)],
        ),
        Query(
            id="q-post-pl-001",
            text=PL,
            language="pl",
            origin="post",
            source_x_id=4,
            event=None,
            split=split,
            judgements=[_judgement(4, True), _judgement(2, True, relevant=False)],
        ),
    ]


def _run(db, embedder=None, tracer=None, queries=None, **kwargs):
    kwargs.setdefault("split", "dev")
    kwargs.setdefault("run_name", "run")
    return run_evaluation(
        db,
        CORPUS,
        queries if queries is not None else _queries(),
        embedder or _embedder(),
        tracer or RecordingTracer(),
        PRICES,
        FixedClock(),
        **kwargs,
    )


def test_runner_reports_metrics_per_mode_and_slice(eval_db):
    result = _run(eval_db)

    modes = result.aggregate.modes
    # post 1 is ranked first by every mode for the English query
    assert modes["fulltext"]["en"].mrr == 1.0
    assert modes["vector"]["en"].mrr == 1.0
    assert modes["hybrid"]["en"].mrr == 1.0
    # for the Polish query post 4 is first in full text and second by vector
    assert modes["fulltext"]["pl"].mrr == 1.0
    assert modes["vector"]["pl"].mrr == pytest.approx(0.5)
    assert modes["hybrid"]["pl"].mrr == 1.0
    assert modes["vector"]["all"].mrr == pytest.approx(0.75)
    assert modes["vector"]["all"].n == 2
    assert modes["vector"]["pl"].recall_at_5 == 1.0
    table = format_table(result.aggregate)
    assert len(table.splitlines()) == 1 + 3 * 3
    rows = [line.split() for line in table.splitlines()]
    assert ["vector", "pl", "1", "1.000", "1.000", "0.500"] in rows


def test_only_reviewed_labels_unless_flag(eval_db):
    reviewed = _run(eval_db)
    everything = _run(eval_db, include_unreviewed=True)

    en = {q["id"]: q for q in reviewed.data["queries"]}["q-post-en-001"]
    assert en["relevant"] == [1]
    en_all = {q["id"]: q for q in everything.data["queries"]}["q-post-en-001"]
    assert en_all["relevant"] == [1, 3]
    # post 3 is retrieved by vector search only after post 4: recall depends on the labels
    assert reviewed.aggregate.modes["fulltext"]["en"].recall_at_5 == 1.0
    assert everything.aggregate.modes["fulltext"]["en"].recall_at_5 == pytest.approx(0.5)
    assert everything.aggregate.modes["vector"]["en"].recall_at_5 == 1.0
    assert everything.data["include_unreviewed"] is True


def test_no_reviewed_labels_fails_with_the_hint(eval_db):
    unreviewed = [
        q.model_copy(
            update={"judgements": [j.model_copy(update={"reviewed": False}) for j in q.judgements]}
        )
        for q in _queries()
    ]
    with pytest.raises(EvaluationError, match="no reviewed labels in the dev split; run review"):
        _run(eval_db, queries=unreviewed)
    with pytest.raises(EvaluationError, match="no reviewed labels in the test split"):
        _run(eval_db, split="test")


def test_result_file_fields(eval_db, tmp_path):
    result = _run(eval_db, k=30, depth=20, limit=5, run_name="my-run")
    data = result.data
    assert data["run_name"] == "my-run"
    assert data["embedding_model"] == MODEL
    assert (data["k"], data["depth"], data["limit"], data["split"]) == (30, 20, 5, "dev")
    assert data["include_unreviewed"] is False
    assert datetime.fromisoformat(data["date"]).tzinfo is not None
    assert set(data["metrics"]["modes"]) == {"fulltext", "vector", "hybrid"}
    assert set(data["metrics"]["modes"]["vector"]) == {"all", "en", "pl"}
    query = data["queries"][1]
    assert query["id"] == "q-post-pl-001"
    assert query["relevant"] == [4]
    assert query["modes"]["fulltext"]["retrieved"][0] == 4
    assert query["modes"]["vector"]["relevant_ranks"] == {"4": 2}
    assert json.loads(json.dumps(data)) == data


def test_unembeddable_corpus_stops_without_file(eval_db, tmp_path):
    deps = _deps(eval_db, {MODEL: AlwaysFailingEmbedder()})
    write_corpus(tmp_path / "corpus.jsonl", CORPUS)
    write_queries(tmp_path / "queries.jsonl", _queries())
    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--split",
            "dev",
            "--queries",
            str(tmp_path / "queries.jsonl"),
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        obj=deps,
    )
    assert result.exit_code == 1
    assert f"error: {len(CORPUS)} corpus posts could not be embedded" in result.stderr
    assert not (tmp_path / "out").exists()


def test_a_query_that_cannot_be_embedded_is_named(eval_db):
    embedder = _embedder()
    embedder.responses = [None] * len(CORPUS) + [RuntimeError("x")] * 3
    with pytest.raises(
        EvaluationError, match="query q-post-en-001 could not be embedded: RuntimeError"
    ):
        _run(eval_db, embedder=embedder)


def test_runner_traces_searches_and_embeddings(eval_db):
    tracer = RecordingTracer()
    _run(eval_db, tracer=tracer)

    queries = _queries()
    query_embeddings = [r for r in tracer.embeddings if r["texts"] in ([EN], [PL])]
    assert sorted(r["texts"][0] for r in query_embeddings) == sorted([EN, PL])
    assert len(query_embeddings) == 2
    searches = tracer.searches
    assert len(searches) == len(queries) * 3
    assert sorted({(r["query"], r["mode"]) for r in searches}) == sorted(
        (q.text, mode) for q in queries for mode in ("fulltext", "vector", "hybrid")
    )
    hybrid = next(r for r in searches if r["query"] == EN and r["mode"] == "hybrid")
    assert set(hybrid["ids_by_mode"]) == {"fulltext", "vector", "hybrid"}
    assert hybrid["ids_by_mode"]["fulltext"] == [1]
    assert tracer.flushed >= 1


def test_default_run_name():
    assert default_run_name("dev", "openai/text-embedding-3-small") == (
        "dev-openai-text-embedding-3-small"
    )


def test_evaluate_command_prints_table_and_writes_the_file(eval_db, tmp_path):
    write_corpus(tmp_path / "corpus.jsonl", CORPUS)
    write_queries(tmp_path / "queries.jsonl", _queries())
    deps = _deps(eval_db, {MODEL: _embedder()})

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--split",
            "dev",
            "--run-name",
            "smoke",
            "--queries",
            str(tmp_path / "queries.jsonl"),
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        obj=deps,
    )

    assert result.exit_code == 0, result.output
    assert "recall@5" in result.stdout and "hybrid" in result.stdout
    written = json.loads((tmp_path / "out" / "smoke.json").read_text())
    assert written["run_name"] == "smoke"


def test_evaluate_command_without_reviewed_labels_fails(eval_db, tmp_path):
    write_corpus(tmp_path / "corpus.jsonl", CORPUS)
    unreviewed = [
        q.model_copy(
            update={"judgements": [j.model_copy(update={"reviewed": False}) for j in q.judgements]}
        )
        for q in _queries()
    ]
    write_queries(tmp_path / "queries.jsonl", unreviewed)
    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--split",
            "dev",
            "--queries",
            str(tmp_path / "queries.jsonl"),
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        obj=_deps(eval_db, {MODEL: _embedder()}),
    )
    assert result.exit_code == 1
    assert "no reviewed labels in the dev split" in result.stderr
    assert not (tmp_path / "out").exists()
