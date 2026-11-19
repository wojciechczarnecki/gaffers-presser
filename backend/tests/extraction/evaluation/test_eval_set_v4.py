from app.extraction.evaluation.cases import (
    REST_POST_CASE,
    SPEC_022_CASES,
    WRONG_TAG_CASE,
    composition_problems_v4,
    load_cases,
)
from app.extraction.evaluation.dataset import EVALS_DIR, load_set

V4 = EVALS_DIR / "v4"
V3 = EVALS_DIR / "v3"
V2_CASES = EVALS_DIR / "v2" / "cases.jsonl"


def _v2_ids() -> set[str]:
    return {case.id for case in load_cases(V2_CASES)}


def _v3_cases():
    return {case.id: case for case in load_cases(V3 / "cases.jsonl")}


def test_schema():
    eval_set = load_set(V4)
    assert eval_set.version == "v4"
    assert eval_set.manifest is not None
    assert eval_set.cases and eval_set.players and eval_set.fixtures
    assert all(case.text.strip() and case.author_handle for case in eval_set.cases)
    assert len({case.id for case in eval_set.cases}) == len(eval_set.cases)
    assert eval_set.manifest.prelabels, "the session judge is named in the manifest"


def test_every_v3_case_in_v4():
    ids = {case.id for case in load_set(V4).cases}
    assert set(_v3_cases()) <= ids


def test_named_gw9_cases():
    by_id = {case.id: case for case in load_set(V4).cases}
    assert all(case_id in by_id for case_id in SPEC_022_CASES)
    assert by_id[REST_POST_CASE].split == "test"


def test_wrong_tag_removed():
    by_id = {case.id: case for case in load_set(V4).cases}
    assert "other_match_absence" not in by_id[WRONG_TAG_CASE].tags


def test_fixtures_snapshot_covers_every_case():
    eval_set = load_set(V4)
    kickoffs = [f.kickoff_at for f in eval_set.fixtures if f.kickoff_at is not None]
    assert min(kickoffs) <= min(case.created_at for case in eval_set.cases)
    assert max(kickoffs) >= max(case.created_at for case in eval_set.cases)
    known = {player.fpl_id for player in eval_set.players}
    used = {e.fpl_id for c in eval_set.cases for e in c.expected_events if e.fpl_id is not None}
    assert used <= known


def test_prelabel_judge_named():
    manifest = load_set(V4).manifest
    assert manifest is not None
    last = manifest.prelabels[-1]
    assert "Claude Code session" in str(last)
    assert "extraction@7" in str(last)


def test_every_case_reviewed():
    unreviewed = [case.id for case in load_set(V4).cases if not case.reviewed]
    assert unreviewed == []


# The cases whose label depends on a judgement the post leaves open (owner decision
# 2026-11-19); the rest of the new and changed cases were labelled by the session.
CONTESTED_CASES = (
    "2115825835525488887",
    "2115481335171108991",
    "2116153319949226164",
    "2116173460590244138",
    "2116110028713164836",
    "2116166387291070901",
    "2116494032461238736",
    "2116162085532938410",
    "2116150477431672942",
    "2115460001162510684",
)


def test_new_and_changed_cases_have_a_review_source():
    v3 = _v3_cases()
    for case in load_set(V4).cases:
        old = v3.get(case.id)
        changed = (
            old is None or case.tags != old.tags or case.expected_events != old.expected_events
        )
        if changed and case.reviewed:
            assert case.review_source in ("session", "owner"), case.id


def test_contested_cases_reviewed_by_the_owner():
    by_id = {case.id: case for case in load_set(V4).cases}
    for case_id in CONTESTED_CASES:
        assert by_id[case_id].reviewed and by_id[case_id].review_source == "owner", case_id


def test_only_contested_cases_wait_for_the_owner():
    unreviewed = {case.id for case in load_set(V4).cases if not case.reviewed}
    assert unreviewed <= set(CONTESTED_CASES)


def test_composition_v4():
    eval_set = load_set(V4)
    assert eval_set.manifest is not None
    problems = composition_problems_v4(
        eval_set.cases, eval_set.players, eval_set.manifest, _v2_ids(), set(_v3_cases())
    )
    assert problems == []
