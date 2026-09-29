import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.extraction.evaluation.cases import (
    EvalCase,
    ExpectedEvent,
    composition_problems,
    load_cases,
    write_cases,
)
from app.extraction.linking import PlayerRecord

EVENT_TYPES = ["out", "doubt", "benched", "confirmed_starter"]
CERTAINTIES = ["confirmed", "likely", "rumour"]
CREATED = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _event(fpl_id: int | None = 1, event_type="out", certainty="confirmed") -> ExpectedEvent:
    return ExpectedEvent(
        mention="Player", fpl_id=fpl_id, event_type=event_type, certainty=certainty
    )


def _case(case_id: str, **overrides) -> EvalCase:
    fields = dict(
        id=case_id,
        author_handle="reporter",
        text=f"text {case_id}",
        created_at=CREATED,
        is_repost=False,
        is_reply=False,
        split="test",
        synthetic=False,
        reviewed=False,
        tags=[],
        expected_events=[],
    )
    fields.update(overrides)
    return EvalCase(**fields)


SNAPSHOT = [
    PlayerRecord(
        season="2026/27",
        fpl_id=1,
        web_name="Player",
        first_name="First",
        second_name="Last",
        team_fpl_id=1,
    )
]


def compliant_cases() -> list[EvalCase]:
    """120 cases: 100 real, 20 synthetic, 36 dev; every rule of the plan satisfied."""
    cases: list[EvalCase] = []
    for i in range(100):
        cases.append(_case(str(1000 + i), split="dev" if i % 10 < 3 else "test"))
    for i in range(20):
        cases.append(
            _case(
                f"syn-{i:03d}",
                synthetic=True,
                author_handle="synthetic_a",
                split="dev" if i < 6 else "test",
            )
        )
    test_cases = [c for c in cases if c.split == "test" and c.expected_events == []]
    # Events for the first test cases: cycle through types and certainties, 6 of each.
    for n in range(12):
        case = test_cases[n]
        case.expected_events = [
            _event(1, EVENT_TYPES[n % 4], CERTAINTIES[n % 3]),
            _event(1, EVENT_TYPES[(n + 1) % 4], CERTAINTIES[(n + 1) % 3]),
        ]
    # Tag cases: three with events for international injuries, three empty ones per category.
    tagged = [c for c in cases if c.split == "test" and not c.expected_events]
    tagged_iter = iter(tagged)
    for tag in ("national_lineup", "womens_lineup", "cup_european_lineup"):
        for _ in range(3):
            next(tagged_iter).tags = [tag]
    for c in test_cases[:3]:
        c.tags = ["international_injury"]
    test_cases[3].tags = ["multi_player"]
    next(tagged_iter).tags = ["ambiguous_name"]
    next(tagged_iter).tags = ["nickname"]
    next(tagged_iter).tags = ["accented_name"]
    next(tagged_iter).is_repost = True
    return cases


def test_compliant_fixture_has_no_problems():
    assert composition_problems(compliant_cases(), SNAPSHOT) == []


def test_schema_accepts_a_full_case():
    case = _case("1", expected_events=[_event()], tags=["multi_player"])
    assert case.expected_events[0].event_type == "out"


@pytest.mark.parametrize(
    "overrides",
    [
        {"split": "validation"},
        {"tags": ["not_a_tag"]},
        {
            "expected_events": [
                {"mention": "X", "fpl_id": 1, "event_type": "injured", "certainty": "confirmed"}
            ]
        },
        {
            "expected_events": [
                {"mention": "X", "fpl_id": 1, "event_type": "out", "certainty": "sure"}
            ]
        },
        {"unknown_field": 1},
    ],
)
def test_schema_rejects_bad_values(overrides):
    with pytest.raises(ValidationError):
        _case("1", **overrides)


def test_schema_rejects_missing_field():
    data = _case("1").model_dump(mode="json")
    del data["reviewed"]
    with pytest.raises(ValidationError):
        EvalCase.model_validate(data)


def test_jsonl_roundtrip(tmp_path):
    cases = [_case("1", expected_events=[_event(None)]), _case("syn-001", synthetic=True)]
    path = tmp_path / "cases.jsonl"
    write_cases(path, cases)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "1"
    assert load_cases(path) == cases


def test_write_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "cases.jsonl"
    write_cases(path, [_case("1")])
    before = path.read_text()

    def interrupted(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr("app.extraction.evaluation.cases.os.replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_cases(path, [_case("1", reviewed=True), _case("2")])
    assert path.read_text() == before


def _mutations():
    def fewer_real(cases):
        return [c for c in cases if not (not c.synthetic and c.split == "test")][:60] + [
            c for c in cases if c.synthetic
        ]

    def too_few_synthetic(cases):
        return [c for c in cases if not c.synthetic or c.id < "syn-005"]

    def dev_share_low(cases):
        for c in cases:
            c.split = "test"
        return cases

    def dev_share_high(cases):
        for c in cases:
            c.split = "dev"
        return cases

    def missing_event_type(cases):
        for c in cases:
            c.expected_events = [e for e in c.expected_events if e.event_type != "doubt"]
        return cases

    def missing_certainty(cases):
        for c in cases:
            c.expected_events = [e for e in c.expected_events if e.certainty != "rumour"]
        return cases

    def no_womens(cases):
        for c in cases:
            c.tags = [t for t in c.tags if t != "womens_lineup"]
        return cases

    def womens_with_events(cases):
        for c in cases:
            if "womens_lineup" in c.tags:
                c.expected_events = [_event()]
        return cases

    def no_international_injury_events(cases):
        for c in cases:
            if "international_injury" in c.tags:
                c.expected_events = []
        return cases

    def no_repost(cases):
        for c in cases:
            c.is_repost = False
        return cases

    def no_nickname(cases):
        for c in cases:
            c.tags = [t for t in c.tags if t != "nickname"]
        return cases

    def duplicate_id(cases):
        cases[1] = cases[1].model_copy(update={"id": cases[0].id})
        return cases

    def unknown_fpl_id(cases):
        cases[0].expected_events = [_event(fpl_id=999999)]
        return cases

    return {
        "few real": (fewer_real, "real"),
        "few synthetic": (too_few_synthetic, "synthetic"),
        "dev share low": (dev_share_low, "dev share"),
        "dev share high": (dev_share_high, "dev share"),
        "event type": (missing_event_type, "doubt"),
        "certainty": (missing_certainty, "rumour"),
        "womens missing": (no_womens, "womens_lineup"),
        "womens with events": (womens_with_events, "womens_lineup"),
        "international injury": (no_international_injury_events, "international_injury"),
        "repost": (no_repost, "repost"),
        "nickname": (no_nickname, "nickname"),
        "duplicate id": (duplicate_id, "duplicate"),
        "unknown fpl id": (unknown_fpl_id, "999999"),
    }


@pytest.mark.parametrize("name", list(_mutations()))
def test_each_composition_rule_fails_on_a_crafted_set(name):
    mutate, keyword = _mutations()[name]
    problems = composition_problems(mutate(compliant_cases()), SNAPSHOT)
    assert any(keyword in problem for problem in problems), problems
