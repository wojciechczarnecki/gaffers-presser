from app.extraction.evaluation.cases import (
    BACKLOG_23_NO_EVENT,
    BACKLOG_23_NOT_CONFIRMED,
    composition_problems_v2,
    load_cases,
)
from app.extraction.evaluation.dataset import EVALS_DIR, load_set

V2 = EVALS_DIR / "v2"
V1_CASES = EVALS_DIR / "v1" / "cases.jsonl"


def _v1_ids() -> set[str]:
    return {case.id for case in load_cases(V1_CASES)}


def test_schema():
    eval_set = load_set(V2)
    assert eval_set.version == "v2"
    assert eval_set.manifest is not None
    assert eval_set.cases and eval_set.players and eval_set.fixtures
    assert all(case.text.strip() and case.author_handle for case in eval_set.cases)
    assert len({case.id for case in eval_set.cases}) == len(eval_set.cases)
    assert eval_set.manifest.prelabels, "the session judge is named in the manifest"


def test_every_v1_case_in_v2():
    ids = {case.id for case in load_set(V2).cases}
    assert _v1_ids() <= ids


def test_every_case_reviewed():
    unreviewed = [case.id for case in load_set(V2).cases if not case.reviewed]
    assert unreviewed == []


def test_composition_v2():
    eval_set = load_set(V2)
    assert eval_set.manifest is not None
    problems = composition_problems_v2(
        eval_set.cases, eval_set.players, eval_set.manifest, _v1_ids()
    )
    assert problems == []


def test_backlog_23_cases_labelled():
    cases = {case.id: case for case in load_set(V2).cases}
    for case_id in BACKLOG_23_NO_EVENT:
        assert cases[case_id].expected_events == []
    assert not [
        e for e in cases[BACKLOG_23_NOT_CONFIRMED].expected_events if e.certainty == "confirmed"
    ]


def test_fixtures_snapshot_covers_every_case():
    eval_set = load_set(V2)
    kickoffs = [f.kickoff_at for f in eval_set.fixtures if f.kickoff_at is not None]
    assert min(kickoffs) <= min(case.created_at for case in eval_set.cases)
    assert max(kickoffs) >= max(case.created_at for case in eval_set.cases)
    known = {player.fpl_id for player in eval_set.players}
    used = {e.fpl_id for c in eval_set.cases for e in c.expected_events if e.fpl_id is not None}
    assert used <= known
