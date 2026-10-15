import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    FreeQuery,
    Judgement,
    load_queries,
    write_queries,
)
from app.retrieval.evaluation.judging import agreement, export_inputs
from tests.retrieval.evaluation.test_judging import BASE, corpus, ids, player


def free(id_="q-post-en-001"):
    return FreeQuery(
        id=id_,
        text="saka injury",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="dev",
        judgements=[],
    )


@pytest.fixture
def world(tmp_path):
    set_dir = tmp_path / "set"
    set_dir.mkdir()
    queries = [player().model_copy(update={"claimed": []}), free()]
    write_queries(set_dir / "queries.jsonl", queries)
    out = tmp_path / "out"
    export_inputs(
        queries,
        {"p-gw6-1": ids(1, 3), "q-post-en-001": ids(4, 5)},
        corpus(),
        out,
        seed=6,
        max_posts=30,
        for_judge=None,
        set_dir=set_dir,
    )
    return set_dir, out


def neutral_for(out, input_id):
    mapping = json.loads((out / "mapping.json").read_text())
    return mapping["inputs"][input_id]["posts"]


def verdict(out, input_id, labels):
    slice_ = json.loads((out / "mapping.json").read_text())["inputs"][input_id]["slice"]
    directory = out / slice_ / "verdicts"
    directory.mkdir(exist_ok=True)
    (directory / f"{input_id}.json").write_text(json.dumps({"labels": labels}))


def all_true(out, input_id):
    verdict(out, input_id, {key: True for key in neutral_for(out, input_id)})


def run_import(world, *args, judge="judge-a"):
    set_dir, out = world
    return CliRunner().invoke(
        app, ["import-judge", "--export", str(out), "--judge-model", judge, *args]
    )


def labels_of(set_dir, query_id):
    query = next(q for q in load_queries(set_dir / "queries.jsonl") if q.id == query_id)
    return query.judgements


def test_a_valid_verdict_stores_one_judgement_per_pair(world):
    set_dir, out = world
    mapped = neutral_for(out, "p-gw6-1")
    verdict(out, "p-gw6-1", {key: i == 0 for i, key in enumerate(mapped)})
    all_true(out, "q-post-en-001")
    result = run_import(world)
    assert result.exit_code == 0, result.output
    assert "labels stored: 5" in result.stdout
    stored = labels_of(set_dir, "p-gw6-1")
    assert len(stored) == 3
    assert all(j.labelled_by == "judge-a" and j.reviewed is False for j in stored)
    by_post = {j.x_id: j.relevant for j in stored}
    assert by_post == {x: i == 0 for i, x in enumerate(mapped.values())}
    assert len(labels_of(set_dir, "q-post-en-001")) == 2


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda labels: {k: v for k, v in list(labels.items())[1:]}, "missing"),
        (lambda labels: {**labels, "P9": True}, "unknown P9"),
        (lambda labels: {**labels, "X1": False}, "unknown X1"),
        (lambda labels: {**labels, "P1": "yes"}, "not a boolean P1"),
    ],
)
def test_a_bad_verdict_fails_its_input_and_the_others_are_stored(world, change, expected):
    set_dir, out = world
    good = {key: True for key in neutral_for(out, "p-gw6-1")}
    verdict(out, "p-gw6-1", change(good))
    all_true(out, "q-post-en-001")
    result = run_import(world)
    assert result.exit_code == 1
    assert "p-gw6-1" in result.stderr and expected in result.stderr
    assert labels_of(set_dir, "p-gw6-1") == []
    assert len(labels_of(set_dir, "q-post-en-001")) == 2


def test_a_verdict_that_is_not_json_or_has_no_labels_fails(world):
    set_dir, out = world
    (out / "player" / "verdicts").mkdir()
    (out / "player" / "verdicts" / "p-gw6-1.json").write_text("not json")
    (out / "free" / "verdicts").mkdir()
    (out / "free" / "verdicts" / "q-post-en-001.json").write_text('{"claims": []}')
    result = run_import(world)
    assert result.exit_code == 1
    assert "not valid JSON" in result.stderr and "no labels object" in result.stderr


def test_an_input_with_no_verdict_is_reported_missing(world):
    set_dir, out = world
    all_true(out, "q-post-en-001")
    result = run_import(world)
    assert result.exit_code == 1
    assert "missing verdict: p-gw6-1" in result.stderr
    assert len(labels_of(set_dir, "q-post-en-001")) == 2


def test_a_second_judge_is_stored_beside_the_first(world):
    set_dir, out = world
    all_true(out, "p-gw6-1")
    all_true(out, "q-post-en-001")
    assert run_import(world, judge="judge-a").exit_code == 0
    mapped = neutral_for(out, "p-gw6-1")
    verdict(out, "p-gw6-1", {key: False for key in mapped})
    assert run_import(world, judge="judge-b").exit_code == 0
    stored = labels_of(set_dir, "p-gw6-1")
    assert len(stored) == 6
    assert {(j.labelled_by, j.relevant) for j in stored} == {("judge-a", True), ("judge-b", False)}


def test_a_reimport_is_a_noop_and_a_changed_label_needs_force(world):
    set_dir, out = world
    all_true(out, "p-gw6-1")
    all_true(out, "q-post-en-001")
    assert run_import(world).exit_code == 0
    before = (set_dir / "queries.jsonl").read_text()
    again = run_import(world)
    assert again.exit_code == 0
    assert "labels stored: 0  unchanged: 5" in again.stdout
    assert (set_dir / "queries.jsonl").read_text() == before

    mapped = neutral_for(out, "p-gw6-1")
    verdict(out, "p-gw6-1", {key: False for key in mapped})
    changed = run_import(world)
    assert changed.exit_code == 1
    assert "differently" in changed.stderr and "--force" in changed.stderr
    assert all(j.relevant for j in labels_of(set_dir, "p-gw6-1"))
    forced = run_import(world, "--force")
    assert forced.exit_code == 0, forced.output
    stored = labels_of(set_dir, "p-gw6-1")
    assert len(stored) == 3 and not any(j.relevant for j in stored)


def judgement(x_id, relevant, by):
    return Judgement(x_id=x_id, relevant=relevant, reviewed=False, labelled_by=by)


def test_agreement_counts_pairs_both_judges_labelled():
    query = player(
        [
            judgement(BASE + 1, True, "a"),
            judgement(BASE + 1, True, "b"),
            judgement(BASE + 2, True, "a"),
            judgement(BASE + 2, False, "b"),
            judgement(BASE + 3, True, "a"),
        ]
    )
    result = agreement([query], "a", "b")
    assert (result.compared, result.agreed, result.share) == (2, 1, 0.5)
    assert [(d.x_id, d.first, d.second) for d in result.disagreements] == [(BASE + 2, True, False)]
    assert agreement([query], "a", "c").share is None


def test_agreement_command_prints_share_n_and_each_disagreement(tmp_path):
    query = player(
        [
            judgement(BASE + 1, True, "a"),
            judgement(BASE + 1, True, "b"),
            judgement(BASE + 2, True, "a"),
            judgement(BASE + 2, False, "b"),
        ]
    )
    write_queries(tmp_path / "queries.jsonl", [query])
    result = CliRunner().invoke(
        app, ["agreement", "--judge", "a", "--judge", "b", "--set", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "agreement: 0.500 over 2 pairs (a vs b)" in result.stdout
    assert "disagreements: 1" in result.stdout
    assert f"p-gw6-1  {BASE + 2}  a=True  b=False" in result.stdout
    bad = CliRunner().invoke(app, ["agreement", "--judge", "a", "--set", str(tmp_path)])
    assert bad.exit_code == 1
    assert Path(tmp_path / "queries.jsonl").exists()
