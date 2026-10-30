import re
from collections import Counter

from app.corroboration.evaluation.cases import (
    V1_CASES_PATH,
    V2_SET_DIR,
    composition_problems,
    load_cases,
)
from app.corroboration.evaluation.dataset import load_manifest

CASES = V2_SET_DIR / "cases.jsonl"
MAX_BUILD_COST_USD = 1.00
SESSION_JUDGE_PREFIX = "claude-opus"


def _v2():
    return load_cases(CASES)


def _new(cases):
    v1_ids = {case.id for case in load_cases(V1_CASES_PATH)}
    return [case for case in cases if case.id not in v1_ids]


def test_schema():
    cases = _v2()
    assert cases
    for case in cases:
        assert case.split in ("dev", "test")
        assert case.post.author_handle and case.anchor.author_handle


def test_every_v1_case_in_v2_with_its_split():
    v1 = {case.id: case for case in load_cases(V1_CASES_PATH)}
    v2 = {case.id: case for case in _v2()}
    assert set(v1) <= set(v2)
    for case_id, case in v1.items():
        assert v2[case_id].split == case.split
        assert v2[case_id].post == case.post and v2[case_id].anchor == case.anchor


def test_at_least_45_new_cases_inside_their_window():
    manifest = load_manifest(V2_SET_DIR)
    windows = manifest.build.windows
    assert [w.gameweek for w in windows] == [6, 7]
    new = _new(_v2())
    assert len(new) >= 45
    for case in new:
        assert any(w.start <= case.anchor.created_at < w.end for w in windows), case.id
        assert any(w.start <= case.post.created_at < w.end for w in windows), case.id


def test_windows_equal_the_production_window_constants():
    from datetime import UTC, datetime

    manifest = load_manifest(V2_SET_DIR)
    got = {w.gameweek: (w.start, w.end) for w in manifest.build.windows}
    assert got[6] == (
        datetime(2026, 10, 3, 10, 0, tzinfo=UTC),
        datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
    )
    assert got[7] == (
        datetime(2026, 10, 12, 21, 0, tzinfo=UTC),
        datetime(2026, 10, 17, 10, 0, tzinfo=UTC),
    )


def test_every_case_records_its_prelabels():
    v1 = {case.id: case for case in load_cases(V1_CASES_PATH)}
    for case in _v2():
        assert case.prelabel is not None and case.labelled_by
        if case.id in v1:
            assert case.v1_prelabel == v1[case.id].expected
        else:
            assert case.v1_prelabel is None


def test_manifest_names_the_session_judge():
    manifest = load_manifest(V2_SET_DIR)
    assert manifest.prelabels
    for entry in manifest.prelabels:
        assert entry.judge.startswith(SESSION_JUDGE_PREFIX)
        assert entry.prompt_version.startswith("corroboration_judge@")
        assert entry.git_commit and entry.git_commit != "unknown"


def test_build_recorded_and_within_one_dollar():
    build = load_manifest(V2_SET_DIR).build
    total = build.embeddings_cost_usd + build.extraction_cost_usd + build.query_embeddings_cost_usd
    assert build.embedding_model == "openai/text-embedding-3-large"
    assert build.extraction_prompt_version
    assert 0 < total <= MAX_BUILD_COST_USD


def test_authors_are_public_handles_and_anchors_mostly_watched():
    # Source posts are the list's posts and the posts they quote, so an anchor can be from an
    # account outside the list; every handle is still a public X handle.
    manifest = load_manifest(V2_SET_DIR)
    watched = {handle.casefold() for handle in manifest.list_handles}
    assert watched
    new = _new(_v2())
    for case in new:
        for handle in (case.anchor.author_handle, case.post.author_handle):
            assert re.fullmatch(r"\w{1,15}", handle), case.id
    in_list = sum(1 for case in new if case.anchor.author_handle.casefold() in watched)
    assert in_list / len(new) >= 0.5


def test_composition():
    assert composition_problems(_v2()) == []


def test_label_counts_are_reported_by_split():
    counts = Counter((case.split, case.expected) for case in _v2())
    assert counts[("test", "contradicts")] >= 5


def test_every_case_reviewed():
    unreviewed = [case.id for case in _v2() if not case.reviewed]
    assert not unreviewed, f"{len(unreviewed)} cases are not reviewed"
