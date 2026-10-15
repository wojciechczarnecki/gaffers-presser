from typer.testing import CliRunner

from app.retrieval.evaluation.cli import app


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
