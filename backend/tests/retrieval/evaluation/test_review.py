from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    CorpusPost,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    load_queries,
    write_corpus,
    write_queries,
)
from app.retrieval.evaluation.review import review_order

T0 = datetime(2026, 9, 26, 17, 30, tzinfo=UTC)
T1 = datetime(2026, 10, 3, 17, 30, tzinfo=UTC)
WHEN = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def j(x_id, relevant, by):
    return Judgement(x_id=x_id, relevant=relevant, reviewed=False, labelled_by=by)


def both(x_id, a, b):
    return [j(x_id, a, "judge-a"), j(x_id, b, "judge-b")]


def player_query():
    judgements = [*both(1, True, True), *both(2, True, False), *both(3, False, False)]
    judgements += both(4, True, True)
    return PlayerQuery(
        id="p-gw6-1",
        split="dev",
        player=PlayerInfo(
            season="2026/27",
            fpl_id=1,
            web_name="Saka",
            first_name="Bukayo",
            second_name="Saka",
            team="Arsenal",
            aliases=[],
        ),
        gameweek=6,
        deadline_at=T1,
        window_start=T0,
        window_end=T1,
        window_rule="alerts",
        previous_last_match_end=None,
        claimed=[],
        judgements=judgements,
    )


def free_query():
    return FreeQuery(
        id="q-post-en-001",
        text="saka injury",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="test",
        judgements=[*both(5, True, False), *both(6, True, True)],
    )


@pytest.fixture
def world(tmp_path):
    posts = [
        CorpusPost(
            x_id=n,
            author_handle=f"author{n}",
            text=f"Post text {n}",
            created_at=WHEN + timedelta(minutes=n),
            is_repost=False,
            is_reply=False,
        )
        for n in range(1, 7)
    ]
    write_corpus(tmp_path / "corpus.jsonl", posts)
    write_queries(tmp_path / "queries.jsonl", [player_query(), free_query()])
    return tmp_path


def run(world, keys, *args):
    return CliRunner().invoke(
        app,
        ["review", "--set", str(world), *args],
        input="".join(f"{k}\n" for k in keys),
    )


def owner(world):
    found = {}
    for query in load_queries(world / "queries.jsonl"):
        for entry in query.judgements:
            if entry.labelled_by == "owner":
                found[(query.id, entry.x_id)] = entry
    return found


def test_the_order_is_every_disagreement_then_a_seeded_sample_of_the_others():
    queries = [player_query(), free_query()]
    order = review_order(queries, 2, 1)
    assert order[:2] == [("p-gw6-1", 2), ("q-post-en-001", 5)]
    sample = order[2:]
    assert len(sample) == 2
    assert set(sample) <= {("p-gw6-1", 1), ("p-gw6-1", 3), ("p-gw6-1", 4), ("q-post-en-001", 6)}
    assert review_order(queries, 2, 1) == order
    assert {tuple(review_order(queries, 2, seed)[2:]) for seed in range(12)} != {tuple(sample)}
    assert review_order(queries, 0, 1) == order[:2]
    assert len(review_order(queries, 99, 1)) == 2 + 4


def test_reviewed_pairs_are_skipped_and_the_sample_is_not_redrawn():
    queries = [player_query(), free_query()]
    full = review_order(queries, 2, 1)
    first_sampled = full[2]
    reviewed = Judgement(x_id=first_sampled[1], relevant=True, reviewed=True, labelled_by="owner")
    marked = [
        q.model_copy(update={"judgements": [*q.judgements, reviewed]})
        if q.id == first_sampled[0]
        else q
        for q in queries
    ]
    assert review_order(marked, 2, 1) == [full[0], full[1], full[3]]
    disagreement = Judgement(x_id=2, relevant=False, reviewed=True, labelled_by="owner")
    done = [
        marked[0].model_copy(update={"judgements": [*marked[0].judgements, disagreement]}),
        marked[1],
    ]
    assert ("p-gw6-1", 2) not in review_order(done, 2, 1)


def test_answers_store_owner_labels_beside_the_judges_and_save_after_each(world):
    result = run(world, ["r", "n", "q"], "--sample", "0")
    assert result.exit_code == 0, result.output
    stored = owner(world)
    assert stored[("p-gw6-1", 2)].relevant is True
    assert stored[("q-post-en-001", 5)].relevant is False
    assert all(e.reviewed for e in stored.values())
    queries = load_queries(world / "queries.jsonl")
    assert len(queries[0].judgements) == 8 + 1  # the judges' entries stay
    assert "relevant: 1  not relevant: 1  skipped: 0" in result.stdout
    assert "reviewed pairs: 2" in result.stdout

    resumed = run(world, ["q"], "--sample", "0")
    assert "nothing to review" in resumed.stdout


def test_skip_and_unknown_actions(world):
    result = run(world, ["x", "s", "r"], "--sample", "0")
    assert result.exit_code == 0, result.output
    assert "unknown action 'x'" in result.stdout
    assert "skipped: 1" in result.stdout
    assert list(owner(world)) == [("q-post-en-001", 5)]


def test_the_file_is_complete_after_an_interrupt(world):
    result = run(world, ["r"], "--sample", "0")  # the input ends: an abort at the second pair
    assert result.exit_code == 130
    assert list(owner(world)) == [("p-gw6-1", 2)]
    assert "reviewed pairs: 1" in result.stdout


def test_the_display_is_blind_and_shows_the_player_or_query_and_the_post(world):
    result = run(world, ["s", "s", "s", "s", "s", "s"], "--sample", "4", "--seed", "1")
    out = result.stdout
    assert "Bukayo Saka (Saka), Arsenal" in out
    assert "gameweek 6" in out and "2026-10-03 19:30" in out
    assert "q-post-en-001  [en, post]  saka injury" in out
    assert "@author2" in out and "Post text 2" in out and "2026-10-01 12:" in out
    for hidden in ("judge-a", "judge-b", "says", "True", "False"):
        assert hidden not in out


def test_split_filter_and_nothing_to_review(world):
    result = run(world, ["q"], "--sample", "0", "--split", "test")
    assert result.exit_code == 0
    assert "q-post-en-001" in result.stdout and "p-gw6-1" not in result.stdout
    bad = run(world, [], "--sample", "0", "--split", "nonsense")
    assert bad.exit_code == 1 and "--split must be dev or test" in bad.stderr


def test_review_needs_no_database_or_key(world, monkeypatch):
    monkeypatch.chdir(world)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert run(world, ["q"], "--sample", "0").exit_code == 0
