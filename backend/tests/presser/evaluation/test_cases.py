import pytest

from app.presser.evaluation.cases import (
    HistoryEntry,
    PresserCase,
    composition_problems,
    load_cases,
    load_history,
    write_cases,
    write_history,
)
from app.presser.writer import PreviousPresser
from tests.presser.evaluation.factories import case, valid_set


def test_a_valid_set_has_no_problems():
    assert composition_problems(valid_set()) == []


def test_round_trip(tmp_path):
    cases = valid_set()
    cases[3] = case("real-3", "dev", "real", previous=[PreviousPresser(2, "Poprzedni tekst")])
    path = tmp_path / "cases.jsonl"
    write_cases(path, cases)
    assert load_cases(path) == cases
    assert not list(tmp_path.glob(".*.tmp"))


def test_history_round_trip(tmp_path):
    entries = [HistoryEntry(league="l1", gameweek=2, text="tekst")]
    write_history(tmp_path / "history.jsonl", entries)
    assert load_history(tmp_path / "history.jsonl") == entries


def test_unknown_field_is_rejected():
    with pytest.raises(ValueError):
        PresserCase.model_validate_json('{"id": "x", "extra": 1}')


def problems_with(mutate):
    cases = valid_set()
    mutate(cases)
    return composition_problems(cases)


def test_case_count_outside_range():
    assert any("cases: 13" in p for p in problems_with(lambda c: [c.pop() for _ in range(3)]))
    assert any(
        "cases:" in p
        for p in problems_with(lambda c: c.extend([case("x1"), case("x2"), case("x3")]))
    )


def test_real_count_must_be_ten():
    problems = problems_with(lambda c: c.__setitem__(0, case("real-0", "dev", "synthetic")))
    assert any("real cases: 9" in p for p in problems)


def test_too_few_synthetic():
    def mutate(cases):
        for index in range(10, 16):
            cases[index] = case(f"fill-{index}", "dev", "real", ["x"])

    assert any("synthetic cases" in p for p in problems_with(mutate))


def test_duplicate_ids():
    assert any(
        "duplicate ids: real-1" in p
        for p in problems_with(lambda c: c.__setitem__(0, case("real-1", "dev", "real")))
    )


def test_split_without_real_or_synthetic():
    def mutate(cases):
        for index, item in enumerate(cases):
            cases[index] = item.model_copy(
                update={"split": "dev" if item.source == "real" else "test"}
            )

    problems = composition_problems(_mutated(mutate))
    assert any("the dev split has no synthetic case" in p for p in problems)
    assert any("the test split has no real case" in p for p in problems)


def _mutated(mutate):
    cases = valid_set()
    mutate(cases)
    return cases


def test_small_test_split():
    def mutate(cases):
        for index in range(len(cases)):
            if cases[index].id in ("real-9", "syn-no_team"):
                cases[index] = cases[index].model_copy(update={"split": "dev"})

    assert any("test split: 6" in p for p in problems_with(mutate))


def test_inconsistent_sheet_is_reported():
    def mutate(cases):
        facts = cases[0].facts.model_copy(deep=True)
        facts.winners[0].net_points = 99
        cases[0] = cases[0].model_copy(update={"facts": facts})

    assert any("real-0: the fact sheet is inconsistent" in p for p in problems_with(mutate))


def test_previous_must_be_earlier():
    def mutate(cases):
        cases[0] = cases[0].model_copy(
            update={"previous": [PreviousPresser(cases[0].facts.gameweek, "ten sam")]}
        )

    assert any("real-0: a previous presser" in p for p in problems_with(mutate))


def test_missing_edge_tag():
    def mutate(cases):
        index = next(i for i, c in enumerate(cases) if "chip_flop" in c.tags)
        cases[index] = cases[index].model_copy(update={"tags": []})

    assert any("no case tagged chip_flop" in p for p in problems_with(mutate))


def test_at_most_two_previous():
    with pytest.raises(ValueError):
        PresserCase(
            id="x",
            split="dev",
            source="real",
            facts=case("y").facts,
            previous=[PreviousPresser(1, "a"), PreviousPresser(2, "b"), PreviousPresser(3, "c")],
        )
