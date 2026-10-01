from datetime import UTC, datetime

import pytest

from app.corroboration.evaluation.cases import (
    LABELS,
    LABELS_NOT_YET_REQUIRED,
    CaseAnchor,
    CasePlayer,
    CasePost,
    JudgeCase,
    composition_problems,
    is_known_miss,
    load_cases,
    parse_cases,
    write_cases,
)
from app.llm.chat import DEFAULT_MODEL

T = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def make_case(
    n: int,
    expected="supports",
    split="test",
    labelled_by="anthropic/claude-haiku-4.5",
    has_player_event=True,
    reviewed=False,
) -> JudgeCase:
    return JudgeCase(
        id=f"jc-{n:03d}",
        split=split,
        player=CasePlayer(fpl_id=2, web_name="Isak", team="Newcastle"),
        anchor=CaseAnchor(
            x_id=1,
            author_handle="anchor_acc",
            created_at=T,
            event_type="doubt",
            certainty="likely",
            text="Isak doubtful",
        ),
        post=CasePost(
            x_id=1000 + n,
            author_handle="lister",
            reposted_author_handle="origin" if n % 2 else None,
            is_repost=bool(n % 2),
            created_at=T,
            text=f"post {n}",
        ),
        expected=expected,
        labelled_by=labelled_by,
        reviewed=reviewed,
        has_player_event=has_player_event,
    )


def valid_set(n: int = 60) -> list[JudgeCase]:
    cases = []
    for i in range(n):
        label = LABELS[i % 4]
        split = "dev" if i % 10 < 3 else "test"
        cases.append(make_case(i, label, split, has_player_event=i % 5 != 0))
    return cases


def test_round_trip(tmp_path):
    cases = valid_set(8)
    path = tmp_path / "cases.jsonl"
    write_cases(path, cases)
    assert load_cases(path) == cases
    assert parse_cases(path.read_text()) == cases


def test_write_is_atomic_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path, valid_set(4))
    assert [p.name for p in tmp_path.iterdir()] == ["cases.jsonl"]


def test_a_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "cases.jsonl"
    write_cases(path, valid_set(4))
    before = path.read_text()

    def boom(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr("app.corroboration.evaluation.cases.os.replace", boom)
    with pytest.raises(OSError):
        write_cases(path, valid_set(5))
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["cases.jsonl"]


def test_a_valid_set_has_no_problems():
    assert composition_problems(valid_set()) == []


def test_size_out_of_range():
    assert any("cases: 40" in p for p in composition_problems(valid_set(40)))
    assert any("cases: 80" in p for p in composition_problems(valid_set(80)))


def test_duplicate_ids():
    cases = valid_set()
    cases[1] = cases[1].model_copy(update={"id": cases[0].id})
    assert any("duplicate ids" in p for p in composition_problems(cases))


def test_dev_share():
    cases = [c.model_copy(update={"split": "dev"}) for c in valid_set()]
    assert any("dev share" in p for p in composition_problems(cases))


def test_a_label_missing_overall_and_in_the_test_split():
    no_related = [c for c in valid_set() if c.expected != "related"]
    problems = composition_problems(no_related + valid_set(12)[:0])
    assert any("no case with the label related" in p for p in problems)
    only_dev = [
        c.model_copy(update={"split": "dev"}) if c.expected == "supports" else c
        for c in valid_set()
    ]
    problems = composition_problems(only_dev)
    assert any("test split has no case with the label supports" in p for p in problems)


def test_a_missing_contradicts_label_is_tolerated_until_backlog_12():
    assert LABELS_NOT_YET_REQUIRED == {"contradicts"}
    base = valid_set()
    count = sum(1 for c in base if c.expected == "contradicts")
    relabelled = [
        c.model_copy(update={"expected": "related"}) if c.expected == "contradicts" else c
        for c in base
    ]
    assert count > 0
    assert composition_problems(relabelled) == []


def test_labelled_by_the_default_model_is_reported():
    cases = valid_set()
    cases[3] = cases[3].model_copy(update={"labelled_by": DEFAULT_MODEL})
    assert any("pre-labelled by the judge's own model" in p for p in composition_problems(cases))


def test_a_set_without_a_known_miss_is_reported():
    cases = [c.model_copy(update={"has_player_event": True}) for c in valid_set()]
    assert any("no known-miss case" in p for p in composition_problems(cases))


def test_known_miss_definition():
    assert is_known_miss(make_case(1, "supports", has_player_event=False))
    assert not is_known_miss(make_case(1, "unrelated", has_player_event=False))
    assert not is_known_miss(make_case(1, "supports", has_player_event=True))
