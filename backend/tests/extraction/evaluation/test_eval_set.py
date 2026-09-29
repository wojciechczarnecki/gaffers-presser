from app.extraction.cli import DEFAULT_CASES_PATH, DEFAULT_PLAYERS_PATH
from app.extraction.evaluation.cases import composition_problems, load_cases
from app.extraction.linking import load_snapshot


def test_schema():
    cases = load_cases(DEFAULT_CASES_PATH)
    assert cases, "the evaluation set is empty"
    assert all(case.text.strip() and case.author_handle for case in cases)


def test_every_case_reviewed():
    unreviewed = [case.id for case in load_cases(DEFAULT_CASES_PATH) if not case.reviewed]
    assert unreviewed == []


def test_composition():
    cases = load_cases(DEFAULT_CASES_PATH)
    players, _ = load_snapshot(DEFAULT_PLAYERS_PATH)
    assert composition_problems(cases, players) == []


def test_relevance_categories():
    cases = load_cases(DEFAULT_CASES_PATH)
    injuries = [c for c in cases if "international_injury" in c.tags and c.expected_events]
    assert len(injuries) >= 3
    for tag in ("national_lineup", "womens_lineup", "cup_european_lineup"):
        empty = [c for c in cases if tag in c.tags and not c.expected_events]
        assert len(empty) >= 3, tag
        assert not [c for c in cases if tag in c.tags and c.expected_events], tag


def test_fpl_ids_in_snapshot():
    cases = load_cases(DEFAULT_CASES_PATH)
    players, _ = load_snapshot(DEFAULT_PLAYERS_PATH)
    known = {player.fpl_id for player in players}
    used = {e.fpl_id for c in cases for e in c.expected_events if e.fpl_id is not None}
    assert used, "no linked expected events"
    assert used <= known
