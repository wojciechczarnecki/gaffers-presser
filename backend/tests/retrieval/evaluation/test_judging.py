import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.content import load_prompt
from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    ClaimedPost,
    CorpusPost,
    EvaluationError,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    SetManifest,
    source_flags,
    write_corpus,
    write_manifest,
    write_queries,
)
from app.retrieval.evaluation.judging import (
    CALL_OVERHEAD_TOKENS,
    REPLY_FORMAT,
    estimate_export,
    export_inputs,
    render_input,
)
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import MODEL
from tests.retrieval.test_cli import _deps

BASE = 9_000_000_000
T0 = datetime(2026, 9, 26, 17, 30, tzinfo=UTC)
T1 = datetime(2026, 10, 3, 17, 30, tzinfo=UTC)
WHEN = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def corpus(count=40):
    return {
        BASE + n: CorpusPost(
            x_id=BASE + n,
            author_handle=f"author{n}",
            text=f"Saka post number {n}\nsecond line",
            created_at=WHEN + timedelta(minutes=n),
            is_repost=False,
            is_reply=False,
        )
        for n in range(1, count + 1)
    }


def player(judgements=()):
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
        claimed=[ClaimedPost(x_id=BASE + 99, created_at=WHEN, stale=False)],
        judgements=list(judgements),
    )


def free(judgements=()):
    return FreeQuery(
        id="q-post-en-001",
        text="saka injury",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="dev",
        judgements=list(judgements),
    )


def ids(start, stop):
    return [BASE + n for n in range(start, stop + 1)]


def export(tmp_path, queries, pooled, **kwargs):
    kwargs.setdefault("seed", 6)
    kwargs.setdefault("max_posts", 30)
    kwargs.setdefault("for_judge", None)
    return export_inputs(queries, pooled, corpus(), tmp_path / "out", set_dir=Path("set"), **kwargs)


def read_input(tmp_path, slice_, input_id):
    return (tmp_path / "out" / slice_ / "inputs" / f"{input_id}.md").read_text()


def neutral_ids(text):
    import re

    return re.findall(r"^\[(P\d+)\]", text, re.M)


def test_export_writes_one_shuffled_neutral_input_per_query(tmp_path):
    pooled = {"p-gw6-1": ids(1, 12), "q-post-en-001": ids(13, 24)}
    summary = export(tmp_path, [player(), free()], pooled)
    assert [i.input_id for i in summary.inputs] == ["p-gw6-1", "q-post-en-001"]
    text = read_input(tmp_path, "player", "p-gw6-1")
    assert neutral_ids(text) == [f"P{n}" for n in range(1, 13)]
    inputs = list((tmp_path / "out").glob("*/inputs/*"))
    assert len(inputs) == 2 + 2  # two inputs and two prompt files
    x_ids = {str(x_id) for x_id in corpus()} | {str(BASE + 99)}
    assert not any(x_id in path.read_text() for path in inputs for x_id in x_ids)
    assert "@author" in text and "Saka post number" in text and "second line" in text
    assert "2026-10-01 12:" in text  # Europe/Warsaw
    mapping = json.loads((tmp_path / "out" / "mapping.json").read_text())
    shown = [
        int(line.split("number ")[1].split()[0]) for line in text.splitlines() if "number" in line
    ]
    assert shown != sorted(shown)
    assert shown == [mapping["inputs"]["p-gw6-1"]["posts"][f"P{n}"] - BASE for n in range(1, 13)]
    # the same seed repeats the order, another one changes it
    export_again = tmp_path / "again"
    export_inputs(
        [player(), free()],
        pooled,
        corpus(),
        export_again,
        seed=6,
        max_posts=30,
        for_judge=None,
        set_dir=Path("set"),
    )
    assert (export_again / "player" / "inputs" / "p-gw6-1.md").read_text() == text
    other = tmp_path / "other"
    export_inputs(
        [player(), free()],
        pooled,
        corpus(),
        other,
        seed=7,
        max_posts=30,
        for_judge=None,
        set_dir=Path("set"),
    )
    assert (other / "player" / "inputs" / "p-gw6-1.md").read_text() != text


def test_mapping_outside_inputs(tmp_path):
    export(tmp_path, [player(), free()], {"p-gw6-1": ids(1, 3), "q-post-en-001": ids(4, 5)})
    out = tmp_path / "out"
    assert (out / "mapping.json").exists()
    assert not list(out.glob("*/inputs/mapping.json")) and not list(out.glob("*/mapping.json"))
    mapping = json.loads((out / "mapping.json").read_text())
    assert mapping["seed"] == 6 and mapping["set_dir"] == "set" and mapping["for_judge"] is None
    assert mapping["prompts"] == {"player": 3, "free": 1}
    player_map = mapping["inputs"]["p-gw6-1"]
    assert player_map["slice"] == "player" and player_map["query_id"] == "p-gw6-1"
    assert sorted(player_map["posts"].values()) == ids(1, 3)
    assert sorted(mapping["inputs"]["q-post-en-001"]["posts"].values()) == ids(4, 5)


def test_long_query_split_by_max_posts(tmp_path):
    summary = export(tmp_path, [player()], {"p-gw6-1": ids(1, 35)})
    assert [(i.input_id, len(i.posts)) for i in summary.inputs] == [
        ("p-gw6-1", 30),
        ("p-gw6-1.2", 5),
    ]
    assert len(neutral_ids(read_input(tmp_path, "player", "p-gw6-1.2"))) == 5
    mapped = [x for i in summary.inputs for x in i.posts.values()]
    assert sorted(mapped) == ids(1, 35)


def test_player_prompt_and_input_name_gameweek_and_deadline(tmp_path):
    export(tmp_path, [player(), free()], {"p-gw6-1": ids(1, 2), "q-post-en-001": ids(3, 4)})
    prompt = load_prompt("retrieval_relevance_player")
    assert prompt.version == 3
    assert (tmp_path / "out/player/inputs/judge-prompt.md").read_text() == prompt.text
    free_prompt = load_prompt("retrieval_relevance_free")
    assert (tmp_path / "out/free/inputs/judge-prompt.md").read_text() == free_prompt.text
    assert "gameweek N" in prompt.text and "denial" in prompt.text
    assert "never from the" in prompt.text  # line-ups in the window are from other matches
    assert "suspended or banned" in prompt.text and "no updates" in prompt.text
    text = read_input(tmp_path, "player", "p-gw6-1")
    assert "Bukayo Saka (Saka), Arsenal" in text
    assert "Gameweek 6, deadline 2026-10-03 19:30 (Europe/Warsaw)" in text
    assert "Window searched: 2026-09-26 19:30 to 2026-10-03 19:30" in text
    assert "# Query\nsaka injury" in read_input(tmp_path, "free", "q-post-en-001")


def test_judged_posts_are_left_out_and_for_judge_reexports_them(tmp_path):
    seen = [Judgement(x_id=BASE + 1, relevant=True, reviewed=False, labelled_by="a")]
    queries = [player(seen)]
    pooled = {"p-gw6-1": ids(1, 3)}
    first = export(tmp_path, queries, pooled)
    assert sorted(first.inputs[0].posts.values()) == ids(2, 3)
    second = export_inputs(
        queries,
        pooled,
        corpus(),
        tmp_path / "second",
        seed=6,
        max_posts=30,
        for_judge="b",
        set_dir=Path("set"),
    )
    assert sorted(second.inputs[0].posts.values()) == ids(1, 3)
    done = export_inputs(
        [
            player(
                [
                    *seen,
                    *(
                        Judgement(x_id=x, relevant=True, reviewed=False, labelled_by="a")
                        for x in ids(2, 3)
                    ),
                ]
            )
        ],
        pooled,
        corpus(),
        tmp_path / "done",
        seed=6,
        max_posts=30,
        for_judge=None,
        set_dir=Path("set"),
    )
    assert done.inputs == [] and done.queries_without_new_posts == 1


def test_export_refuses_a_non_empty_out(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "old.txt").write_text("x")
    with pytest.raises(EvaluationError, match="not empty"):
        export(tmp_path, [player()], {"p-gw6-1": ids(1, 2)})


def test_estimate_counts_calls_and_tokens(tmp_path):
    export(tmp_path, [player(), free()], {"p-gw6-1": ids(1, 35), "q-post-en-001": ids(36, 38)})
    out = tmp_path / "out"
    estimate = estimate_export(out)
    prompt_chars = len((out / "player/inputs/judge-prompt.md").read_text())
    expected = sum(
        (prompt_chars + len(p.read_text()) + len(REPLY_FORMAT)) // 4 + CALL_OVERHEAD_TOKENS
        for p in (out / "player/inputs").glob("*.md")
        if p.name != "judge-prompt.md"
    )
    assert estimate.slices["player"].calls == 2
    assert estimate.slices["player"].tokens == expected
    assert estimate.slices["free"].calls == 1
    assert estimate.calls == 3
    assert estimate.tokens == expected + estimate.slices["free"].tokens
    assert CALL_OVERHEAD_TOKENS == 3000


def test_estimate_needs_an_export(tmp_path):
    with pytest.raises(EvaluationError, match="no export"):
        estimate_export(tmp_path)


def test_the_reply_format_matches_the_scripts_retrieval_format():
    script = (Path(__file__).resolve().parents[4] / "scripts" / "session_judge.sh").read_text()
    assert REPLY_FORMAT in script


def _write_set(tmp_path):
    posts = source_flags(list(corpus(12).values()), None)
    write_corpus(tmp_path / "corpus.jsonl", posts)
    write_manifest(tmp_path, SetManifest(version="v2"))
    items = [
        player().model_copy(update={"claimed": []}),
        free().model_copy(update={"split": "test"}),
    ]
    write_queries(tmp_path / "queries.jsonl", items)


def _export(eval_db, tmp_path, *args):
    return CliRunner().invoke(
        app,
        ["export-judge-inputs", "--out", str(tmp_path / "out"), "--set", str(tmp_path), *args],
        obj=_deps(eval_db, {MODEL: FakeEmbedder()}),
    )


def test_the_cli_pools_exports_and_prints_the_estimate(eval_db, tmp_path):
    _write_set(tmp_path)
    result = _export(eval_db, tmp_path)
    assert result.exit_code == 0, result.output
    assert "estimated embedding fake/embed:" in result.stdout
    assert "inputs: 2" in result.stdout
    assert "total: 2 calls" in result.stdout
    assert (tmp_path / "out/player/inputs/p-gw6-1.md").exists()
    assert (tmp_path / "out/free/inputs/q-post-en-001.md").exists()
    shown = CliRunner().invoke(app, ["estimate", "--export", str(tmp_path / "out")])
    assert shown.exit_code == 0
    assert "player: 1 calls" in shown.stdout and "free: 1 calls" in shown.stdout

    again = _export(eval_db, tmp_path)
    assert again.exit_code == 1 and "not empty" in again.stderr


def test_the_cli_split_and_query_sample_and_cost_cap(eval_db, tmp_path):
    _write_set(tmp_path)
    only_dev = _export(eval_db, tmp_path, "--split", "dev")
    assert only_dev.exit_code == 0, only_dev.output
    assert not (tmp_path / "out/free").exists()

    out2 = tmp_path / "out2"
    sampled = CliRunner().invoke(
        app,
        ["export-judge-inputs", "--out", str(out2), "--set", str(tmp_path), "--query-sample", "1"],
        obj=_deps(eval_db, {MODEL: FakeEmbedder()}),
    )
    assert sampled.exit_code == 0, sampled.output
    assert "inputs: 1" in sampled.stdout

    capped = CliRunner().invoke(
        app,
        [
            "export-judge-inputs",
            "--out",
            str(tmp_path / "out3"),
            "--set",
            str(tmp_path),
            "--max-cost-usd",
            "0.0000001",
        ],
        obj=_deps(eval_db, {MODEL: FakeEmbedder()}),
    )
    assert capped.exit_code == 1 and "above --max-cost-usd" in capped.stderr
    assert not (tmp_path / "out3").exists()


def test_the_query_sample_is_seeded(eval_db, tmp_path):
    posts = source_flags(list(corpus(12).values()), None)
    write_corpus(tmp_path / "corpus.jsonl", posts)
    write_manifest(tmp_path, SetManifest(version="v2"))
    items = [free().model_copy(update={"id": f"q-post-en-{n:03}"}) for n in range(1, 9)]
    write_queries(tmp_path / "queries.jsonl", items)

    def sampled(out, seed):
        result = CliRunner().invoke(
            app,
            [
                "export-judge-inputs",
                "--out",
                str(tmp_path / out),
                "--set",
                str(tmp_path),
                "--query-sample",
                "3",
                "--seed",
                str(seed),
            ],
            obj=_deps(eval_db, {MODEL: FakeEmbedder()}),
        )
        assert result.exit_code == 0, result.output
        return sorted(path.stem for path in (tmp_path / out).glob("free/inputs/q-*.md"))

    first = sampled("a", 6)
    assert len(first) == 3
    assert sampled("b", 6) == first
    assert any(sampled(f"c{seed}", seed) != first for seed in (7, 8, 9))


def test_a_post_starting_with_a_neutral_id_adds_no_phantom_id(tmp_path):
    posts = list(corpus(2).values())
    posts[0] = posts[0].model_copy(update={"text": "[P31] looks like an ID\n[P32] again"})
    text = render_input(free(), posts)
    assert neutral_ids(text) == ["P1", "P2"]
    assert "  [P31] looks like an ID" in text
