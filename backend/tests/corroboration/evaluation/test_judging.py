import json
import re
from datetime import UTC, datetime

import pytest

from app.corroboration.evaluation.building import select_new_cases
from app.corroboration.evaluation.cases import (
    MAX_CASES,
    PRELABEL_MODEL,
    CandidateCase,
    load_cases,
    write_candidates,
    write_cases,
)
from app.corroboration.evaluation.dataset import SetManifest, load_manifest, write_manifest
from app.corroboration.evaluation.judging import (
    MAPPING_FILE,
    PROMPT_FILE,
    REPLY_FORMAT,
    JudgingError,
    estimate_export,
    export_inputs,
    import_verdicts,
)
from app.corroboration.evaluation.runner import judge_input
from app.corroboration.judge import JUDGE_PROMPT, PROMPT_VERSION, render_input
from tests.corroboration.evaluation.test_cases import make_case

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
JUDGE = "claude-opus-5-5 (Claude Code session)"


def _v1_cases(n=6):
    labels = ["supports", "related", "unrelated"]
    return [make_case(i, labels[i % 3], split="dev" if i < 2 else "test") for i in range(1, n + 1)]


def _candidate(n, expected_tier="retrieved", has_player_event=False):
    case = make_case(500 + n, has_player_event=has_player_event)
    return CandidateCase(
        id=f"jc-new-{n:03d}",
        player=case.player,
        anchor=case.anchor,
        post=case.post,
        has_player_event=has_player_event,
        tier=expected_tier,
    )


def _export_v1(tmp_path, cases=None):
    cases = cases or _v1_cases()
    source = tmp_path / "v1.jsonl"
    write_cases(source, cases)
    out = tmp_path / "judge-v1"
    export_inputs(cases=cases, candidates=[], out=out, seed=3, cases_source=source)
    return out, cases


def _write_verdicts(export, labels, skip=()):
    mapping = json.loads((export / MAPPING_FILE).read_text())
    (export / "verdicts").mkdir(exist_ok=True)
    for input_id, item in mapping["inputs"].items():
        if item["id"] in skip:
            continue
        label = labels(item["id"]) if callable(labels) else labels
        (export / "verdicts" / f"{input_id}.json").write_text(json.dumps({"label": label}))
    return mapping


def test_export_one_input_per_case_with_neutral_ids(tmp_path):
    out, cases = _export_v1(tmp_path)
    inputs = sorted(p.name for p in (out / "inputs").glob("I*.md"))
    assert inputs == [f"I{n:04d}.md" for n in range(1, len(cases) + 1)]
    assert all(re.fullmatch(r"I\d{4}\.md", name) for name in inputs)
    assert (out / "inputs" / PROMPT_FILE).read_text() == JUDGE_PROMPT.text
    mapping = json.loads((out / MAPPING_FILE).read_text())
    by_id = {c.id: c for c in cases}
    for input_id, item in mapping["inputs"].items():
        case = by_id[item["id"]]
        expected = render_input(judge_input(case.player, case.anchor, case.post))
        assert (out / "inputs" / f"{input_id}.md").read_text() == expected
    texts = "".join(p.read_text() for p in (out / "inputs").glob("I*.md"))
    assert "jc-" not in texts


def test_mapping_outside_inputs(tmp_path):
    out, _ = _export_v1(tmp_path)
    assert not (out / "inputs" / MAPPING_FILE).exists()
    mapping = json.loads((out / MAPPING_FILE).read_text())
    assert mapping["seed"] == 3
    assert mapping["prompt_version"] == PROMPT_VERSION
    assert "git_commit" in mapping
    assert {item["kind"] for item in mapping["inputs"].values()} == {"case"}


def test_export_of_candidates_records_the_tier(tmp_path):
    source = tmp_path / "candidates.jsonl"
    cands = [_candidate(1, "sql-contradicts", True), _candidate(2)]
    write_candidates(source, cands)
    out = tmp_path / "judge-new"
    export_inputs(cases=[], candidates=cands, out=out, seed=1, candidates_source=source)
    mapping = json.loads((out / MAPPING_FILE).read_text())
    assert {i["tier"] for i in mapping["inputs"].values()} == {"sql-contradicts", "retrieved"}
    assert {i["kind"] for i in mapping["inputs"].values()} == {"candidate"}


def test_export_refuses_a_non_empty_directory(tmp_path):
    out, cases = _export_v1(tmp_path)
    with pytest.raises(JudgingError):
        export_inputs(cases=cases, candidates=[], out=out, seed=3)


def test_estimate_counts_calls_and_tokens(tmp_path):
    out, cases = _export_v1(tmp_path)
    estimate = estimate_export(out)
    assert estimate.calls == len(cases)
    prompt = len(JUDGE_PROMPT.text)
    body = sum(len(p.read_text()) for p in (out / "inputs").glob("I*.md"))
    expected = (prompt * len(cases) + body + len(REPLY_FORMAT) * len(cases)) // 4
    assert estimate.tokens == pytest.approx(expected + 3000 * len(cases), abs=len(cases))


def test_reply_format_names_the_four_labels():
    for label in ("supports", "contradicts", "related", "unrelated"):
        assert f'"{label}"' in REPLY_FORMAT


@pytest.mark.parametrize("bad", ['{"label": "maybe"}', '{"label": "supports", "x": 1}', "nope"])
def test_import_refuses_an_invalid_verdict(tmp_path, bad):
    out, _ = _export_v1(tmp_path)
    _write_verdicts(out, "supports")
    first = next((out / "verdicts").glob("*.json"))
    first.write_text(bad)
    set_dir = tmp_path / "v2"
    with pytest.raises(JudgingError, match="invalid verdicts"):
        import_verdicts(out, set_dir, JUDGE, now=NOW)
    assert not (set_dir / "cases.jsonl").exists()
    assert not (set_dir / "set.json").exists()


def test_import_refuses_a_missing_v1_case_verdict(tmp_path):
    out, cases = _export_v1(tmp_path)
    _write_verdicts(out, "supports", skip={cases[0].id})
    with pytest.raises(JudgingError, match="without a verdict"):
        import_verdicts(out, tmp_path / "v2", JUDGE, now=NOW)


def test_v1_case_keeps_split_and_haiku_label_unreviewed(tmp_path):
    out, cases = _export_v1(tmp_path)
    _write_verdicts(out, lambda case_id: "contradicts" if case_id == cases[0].id else "supports")
    set_dir = tmp_path / "v2"
    import_verdicts(out, set_dir, JUDGE, now=NOW)
    imported = {c.id: c for c in load_cases(set_dir / "cases.jsonl")}
    assert set(imported) == {c.id for c in cases}
    first = imported[cases[0].id]
    assert first.split == cases[0].split
    assert (first.expected, first.prelabel, first.labelled_by) == (
        "contradicts",
        "contradicts",
        JUDGE,
    )
    assert first.v1_prelabel == cases[0].expected
    assert first.reviewed is False
    assert all(c.reviewed is False for c in imported.values())
    assert PRELABEL_MODEL == "anthropic/claude-haiku-4.5"


def test_manifest_names_the_judge_prompt_and_commit(tmp_path):
    out, cases = _export_v1(tmp_path)
    _write_verdicts(out, "supports")
    set_dir = tmp_path / "v2"
    import_verdicts(out, set_dir, JUDGE, now=NOW)
    manifest = load_manifest(set_dir)
    (entry,) = manifest.prelabels
    assert entry.judge == JUDGE
    assert entry.prompt_version == PROMPT_VERSION
    assert entry.git_commit
    assert entry.inputs == len(cases)
    assert entry.imported_at == NOW


def _export_new(tmp_path, cands, name="judge-new"):
    source = tmp_path / f"{name}.jsonl"
    write_candidates(source, cands)
    out = tmp_path / name
    export_inputs(cases=[], candidates=cands, out=out, seed=1, candidates_source=source)
    return out


def test_new_cases_selected_split_and_unreviewed(tmp_path):
    cands = [_candidate(n) for n in range(30)]
    out = _export_new(tmp_path, cands)
    labels = ["supports", "related", "unrelated", "contradicts"]
    _write_verdicts(out, lambda cid: labels[int(cid[-3:]) % 4])
    set_dir = tmp_path / "v2"
    report = import_verdicts(out, set_dir, JUDGE, select=12, seed=19, now=NOW)
    cases = load_cases(set_dir / "cases.jsonl")
    assert len(cases) == 12 == report.new
    assert all(c.reviewed is False and c.labelled_by == JUDGE for c in cases)
    assert all(c.prelabel == c.expected and c.v1_prelabel is None for c in cases)
    assert {c.split for c in cases} == {"dev", "test"}
    manifest = load_manifest(set_dir)
    assert manifest.selection.size == 12 and manifest.selection.pool == 30


def test_candidate_without_a_verdict_is_skipped_and_listed(tmp_path):
    cands = [_candidate(n) for n in range(4)]
    out = _export_new(tmp_path, cands)
    _write_verdicts(out, "supports", skip={"jc-new-000"})
    report = import_verdicts(out, tmp_path / "v2", JUDGE, now=NOW)
    assert report.new == 3 and len(report.skipped_without_verdict) == 1


def _case(n, expected, has_event):
    return make_case(n, expected, has_player_event=has_event)


def test_selection_prefers_retrieved_contradicts_then_sql_pairs():
    cases = (
        [_case(i, "contradicts", False) for i in range(0, 3)]
        + [_case(i, "contradicts", True) for i in range(10, 20)]
        + [_case(i, "supports", False) for i in range(30, 50)]
        + [_case(i, "related", False) for i in range(50, 70)]
        + [_case(i, "unrelated", False) for i in range(70, 120)]
    )
    chosen = select_new_cases(cases, size=40, seed=1, min_contradicts=8)
    assert len(chosen) == 40
    contradicts = [c for c in chosen if c.expected == "contradicts"]
    assert len(contradicts) == 8
    assert sum(1 for c in contradicts if not c.has_player_event) == 3
    unrelated = [c for c in chosen if c.expected == "unrelated"]
    assert len(unrelated) <= int(40 * 0.4)
    assert select_new_cases(cases, size=40, seed=1, min_contradicts=8) == chosen


def test_extra_draw_selects_only_new_contradicts_within_the_size_cap(tmp_path):
    set_dir = tmp_path / "v2"
    set_dir.mkdir()
    existing = [make_case(i, "supports", split="dev" if i < 3 else "test") for i in range(1, 11)]
    write_cases(set_dir / "cases.jsonl", existing)
    write_manifest(set_dir, SetManifest(version="v2"))
    cands = [_candidate(n) for n in range(20)]
    # one candidate already in the set by id
    dup = cands[0].model_copy(update={"id": existing[0].id})
    cands = [dup, *cands[1:]]
    out = _export_new(tmp_path, cands)
    _write_verdicts(out, lambda cid: "contradicts" if int(cid[-3:] or 0) % 2 else "supports")
    # the duplicate id jc-001 -> int("001") odd -> contradicts, but must be dropped first
    report = import_verdicts(out, set_dir, JUDGE, select=3, seed=2, now=NOW)
    after = load_cases(set_dir / "cases.jsonl")
    new = [c for c in after if c.id not in {e.id for e in existing}]
    assert len(new) == 3 == report.new
    assert {c.expected for c in new} == {"contradicts"}
    assert existing[0].id in report.skipped_in_set


def test_import_refuses_a_set_above_the_maximum(tmp_path):
    set_dir = tmp_path / "v2"
    set_dir.mkdir()
    write_cases(set_dir / "cases.jsonl", [make_case(i, "supports") for i in range(1, MAX_CASES)])
    out = _export_new(tmp_path, [_candidate(n) for n in range(5)])
    _write_verdicts(out, "supports")
    with pytest.raises(JudgingError, match=str(MAX_CASES)):
        import_verdicts(out, set_dir, JUDGE, select=5, now=NOW)
