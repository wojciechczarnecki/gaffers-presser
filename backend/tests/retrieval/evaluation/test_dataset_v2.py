import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    DEFAULT_QUERIES_PATH,
    V1_DIR,
    ClaimedPost,
    CorpusPost,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    SetError,
    SetManifest,
    carry_v1,
    load_queries,
    load_set,
    write_corpus,
    write_manifest,
    write_queries,
)

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def player_query(**fields) -> PlayerQuery:
    base = {
        "id": "p-gw6-1",
        "split": "dev",
        "player": PlayerInfo(
            season="2026-27",
            fpl_id=1,
            web_name="B.Fernandes",
            first_name="Bruno",
            second_name="Borges Fernandes",
            team="Man Utd",
            aliases=["Bruno"],
        ),
        "gameweek": 6,
        "deadline_at": T1,
        "window_start": T0,
        "window_end": T1,
        "window_rule": "alerts",
        "previous_last_match_end": T0,
        "claimed": [
            ClaimedPost(x_id=10, created_at=T0, stale=True),
            ClaimedPost(x_id=11, created_at=T1, stale=False),
        ],
        "judgements": [Judgement(x_id=12, relevant=True, reviewed=False, labelled_by="j")],
    }
    return PlayerQuery(**(base | fields))


def free_query(id_="q-post-en-001", judgements=None) -> FreeQuery:
    return FreeQuery(
        id=id_,
        text="saka injury",
        language="en",
        origin="post",
        source_x_id=1,
        event=None,
        split="test",
        judgements=judgements or [Judgement(x_id=1, relevant=True, reviewed=True, labelled_by="m")],
    )


def post(x_id: int) -> CorpusPost:
    return CorpusPost(
        x_id=x_id,
        author_handle="a",
        text="t",
        created_at=T0,
        is_repost=False,
        is_reply=False,
        is_source=True,
    )


def test_player_query_round_trips_every_field(tmp_path):
    original = player_query(window_rule="after-last-match")
    path = tmp_path / "queries.jsonl"
    write_queries(path, [original, free_query()])
    loaded = load_queries(path)
    assert loaded[0] == original
    assert isinstance(loaded[0], PlayerQuery)
    assert [c.stale for c in loaded[0].claimed] == [True, False]
    assert loaded[0].window_rule == "after-last-match"
    assert loaded[0].window_end == loaded[0].deadline_at
    assert isinstance(loaded[1], FreeQuery)


def test_v1_files_load_unchanged():
    loaded = load_set(V1_DIR)
    assert loaded.version == "v1"
    assert loaded.manifest is None
    assert loaded.queries and all(q.kind == "free" for q in loaded.queries)
    assert loaded.corpus and loaded.corpus[0].is_source is None
    raw = [json.loads(line) for line in DEFAULT_QUERIES_PATH.read_text().splitlines()]
    assert "kind" not in raw[0]


def test_set_version_comes_from_the_manifest_when_present(tmp_path):
    write_corpus(tmp_path / "corpus.jsonl", [post(1)])
    write_manifest(tmp_path, SetManifest(version="v2", list_handles=["a"], corpus_until=T1))
    loaded = load_set(tmp_path)
    assert loaded.version == "v2"
    assert loaded.manifest.list_handles == ["a"]
    assert loaded.queries == []


def test_unknown_kind_is_rejected(tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text(json.dumps({"kind": "other", "id": "x"}) + "\n")
    with pytest.raises(ValidationError):
        load_queries(path)


def test_carry_v1_keeps_judgements_and_labelled_by():
    judged = free_query(
        judgements=[
            Judgement(x_id=1, relevant=True, reviewed=False, labelled_by="openai/gpt-6-luna"),
            Judgement(x_id=2, relevant=False, reviewed=True, labelled_by="owner"),
        ]
    )
    result = carry_v1([judged], [player_query()], {1, 2, 3})
    assert [q.id for q in result] == ["p-gw6-1", "q-post-en-001"]
    assert result[1].judgements == judged.judgements
    with pytest.raises(SetError, match="already holds free queries"):
        carry_v1([judged], result, {1, 2})
    with pytest.raises(SetError, match="1 posts"):
        carry_v1([judged], [], {1})


def test_carry_v1_command(tmp_path):
    v1 = tmp_path / "v1"
    v1.mkdir()
    write_corpus(v1 / "corpus.jsonl", [post(1)])
    write_queries(v1 / "queries.jsonl", [free_query("a"), free_query("b")])
    v2 = tmp_path / "v2"
    v2.mkdir()
    write_corpus(v2 / "corpus.jsonl", [post(1)])
    write_queries(v2 / "queries.jsonl", [player_query()])
    args = ["carry-v1", "--from", str(v1), "--set", str(v2)]

    first = CliRunner().invoke(app, [*args, "--first", "1"])
    assert first.exit_code == 0, first.output
    assert [q.id for q in load_queries(v2 / "queries.jsonl")] == ["p-gw6-1", "a"]

    again = CliRunner().invoke(app, args)
    assert again.exit_code == 1
    assert "--force" in again.stderr
    forced = CliRunner().invoke(app, [*args, "--force"])
    assert forced.exit_code == 0, forced.output
    assert [q.id for q in load_queries(v2 / "queries.jsonl")] == ["p-gw6-1", "a", "b"]

    write_corpus(v2 / "corpus.jsonl", [post(5)])
    missing = CliRunner().invoke(app, [*args, "--force"])
    assert missing.exit_code == 1
    assert "missing from the v2 corpus" in missing.stderr
