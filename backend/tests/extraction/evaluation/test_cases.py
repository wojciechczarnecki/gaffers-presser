import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.extraction.evaluation.cases import (
    EvalCase,
    ExpectedEvent,
    composition_problems,
    composition_problems_v2,
    composition_problems_v3,
    load_cases,
    write_cases,
)
from app.extraction.evaluation.dataset import SetManifest
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

    monkeypatch.setattr("app.core.eval_results.os.replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_cases(path, [_case("1", reviewed=True), _case("2")])
    assert path.read_text() == before
    assert list(tmp_path.iterdir()) == [path]  # no temporary file left behind


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


IN_WINDOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
NO_EVENT_23 = ("2104646876389650860", "2106010889262436608")
NOT_CONFIRMED_23 = "2104158837540622748"


def v2_set() -> tuple[list[EvalCase], SetManifest, set[str]]:
    cases = compliant_cases()
    v1_ids = {c.id for c in cases}
    special = {
        NO_EVENT_23[0]: [],
        NO_EVENT_23[1]: [],
        NOT_CONFIRMED_23: [_event(1, "doubt", "likely")],
    }
    new: list[EvalCase] = []
    for case_id, events in special.items():
        new.append(
            _case(
                case_id,
                author_handle="Watched",
                created_at=IN_WINDOW,
                split="dev" if int(case_id) % 10 < 3 else "test",
                expected_events=events,
            )
        )
    other_ids = []
    for i in range(77):
        case_id = str(2200000000000000000 + i * 7)
        split = "dev" if int(case_id) % 10 < 3 else "test"
        tags = []
        events = []
        tagged = {"test": 6, "dev": 3}[split]
        if sum(1 for c in new if c.split == split and c.tags) < tagged:
            tags = ["other_match_lineup"]
            other_ids.append(case_id)
        elif split == "test" and i % 2 == 0:
            events = [_event(1, "benched", "likely")]
        new.append(
            _case(
                case_id,
                author_handle="watched",
                created_at=IN_WINDOW,
                split=split,
                tags=tags,
                expected_events=events,
            )
        )
    manifest = SetManifest(
        version="v2",
        list_handles=["Watched"],
        backlog_cases={"23": list(special), "54": other_ids[:1]},
        backlog_54_lineups=other_ids[:1],
    )
    return [*cases, *new], manifest, v1_ids


def _problems(cases, manifest, v1_ids):
    return composition_problems_v2(cases, SNAPSHOT, manifest, v1_ids)


def test_composition_v2_accepts_a_valid_set():
    assert _problems(*v2_set()) == []


def test_composition_v2_missing_v1_id():
    cases, manifest, v1_ids = v2_set()
    cases = [c for c in cases if c.id != "1000"]
    assert any("v1 cases missing" in p for p in _problems(cases, manifest, v1_ids))


def test_composition_v2_too_few_new_cases():
    cases, manifest, v1_ids = v2_set()
    for case in cases[-30:]:
        case.created_at = datetime(2026, 9, 1, tzinfo=UTC)
    assert any("new real cases" in p for p in _problems(cases, manifest, v1_ids))


def test_composition_v2_too_few_lineups_in_the_window():
    cases, manifest, v1_ids = v2_set()
    for case in cases:
        for event in case.expected_events:
            if event.event_type == "benched" and case.id not in v1_ids:
                event.event_type = "out"
    assert any("GW6-GW7" in p for p in _problems(cases, manifest, v1_ids))


def test_composition_v2_too_few_tagged_cases():
    cases, manifest, v1_ids = v2_set()
    for case in cases:
        if case.tags == ["other_match_lineup"]:
            case.tags = []
    problems = _problems(cases, manifest, v1_ids)
    assert any("other_match_lineup cases: 0" in p for p in problems)
    assert any("other_match_lineup cases in test: 0" in p for p in problems)


def test_composition_v2_tagged_case_with_an_event():
    cases, manifest, v1_ids = v2_set()
    tagged = next(c for c in cases if "other_match_lineup" in c.tags)
    tagged.expected_events = [_event(1, "benched", "likely")]
    assert any("must have no expected events" in p for p in _problems(cases, manifest, v1_ids))


def test_composition_v2_wrong_23_label():
    cases, manifest, v1_ids = v2_set()
    by_id = {c.id: c for c in cases}
    by_id[NO_EVENT_23[0]].expected_events = [_event(1, "out", "likely")]
    by_id[NOT_CONFIRMED_23].expected_events = [_event(1, "doubt", "confirmed")]
    problems = _problems(cases, manifest, v1_ids)
    assert any(NO_EVENT_23[0] in p and "no expected events" in p for p in problems)
    assert any(NOT_CONFIRMED_23 in p and "confirmed" in p for p in problems)


def test_composition_v2_untagged_54_lineup():
    cases, manifest, v1_ids = v2_set()
    plain = next(c for c in cases if c.id not in v1_ids and not c.tags and c.id not in NO_EVENT_23)
    manifest.backlog_54_lineups.append(plain.id)
    assert any("not tagged" in p for p in _problems(cases, manifest, v1_ids))


def test_composition_v2_wrong_split():
    cases, manifest, v1_ids = v2_set()
    new = next(c for c in cases if c.id not in v1_ids and c.split == "test")
    new.split = "dev"
    assert any(new.id in p and "split" in p for p in _problems(cases, manifest, v1_ids))


def test_composition_v2_unknown_author():
    cases, manifest, v1_ids = v2_set()
    new = next(c for c in cases if c.id not in v1_ids)
    new.author_handle = "stranger"
    assert any(new.id in p and "watched list" in p for p in _problems(cases, manifest, v1_ids))


def v3_set():
    base = compliant_cases()
    # the base cycles four types in test; five available events come from the new cases
    v2_ids = {c.id for c in base}
    new: list[EvalCase] = []
    start = 2300000000000000000
    for decade in range(2):
        for offset in range(10):
            case_id = str(start + decade * 10 + offset)
            dev = offset < 3
            tags: list = []
            events: list[ExpectedEvent] = []
            if dev:
                if offset == 0 or (decade == 0 and offset == 1):
                    tags = ["other_match_absence"]
                else:
                    tags = ["cup_injury"]
                    events = [_event(1, "doubt", "likely")]
            elif decade == 0:
                if offset < 8:
                    events = [_event(1, "available", "confirmed")]
                else:
                    tags = ["other_match_absence"]
            elif offset < 6:
                tags = ["other_match_absence"]
            else:
                events = [_event(1, "available", "likely")]
            new.append(
                _case(
                    case_id,
                    author_handle="watched",
                    split="dev" if dev else "test",
                    tags=tags,
                    expected_events=events,
                )
            )
    manifest = SetManifest(
        version="v3", list_handles=["Watched"], backlog_cases={"24": [new[0].id, new[5].id]}
    )
    return [*base, *new], manifest, v2_ids


def _problems_v3(cases, manifest, v2_ids):
    return composition_problems_v3(cases, SNAPSHOT, manifest, v2_ids)


def test_composition_v3_accepts_a_valid_set():
    assert _problems_v3(*v3_set()) == []


def test_composition_v3_needs_available_events_in_test():
    cases, manifest, v2_ids = v3_set()
    for case in cases:
        case.expected_events = [e for e in case.expected_events if e.event_type != "available"]
    assert any("available events" in p for p in _problems_v3(cases, manifest, v2_ids))


def test_composition_v3_other_match_absence_counts_and_no_absence_event():
    cases, manifest, v2_ids = v3_set()
    tagged = [c for c in cases if "other_match_absence" in c.tags]
    tagged[0].expected_events = [_event(1, "out", "likely")]
    problems = _problems_v3(cases, manifest, v2_ids)
    assert any(tagged[0].id in p and "no out or doubt" in p for p in problems)
    for case in tagged:
        case.tags = []
    problems = _problems_v3(cases, manifest, v2_ids)
    assert any("other_match_absence cases: 0" in p for p in problems)
    assert any("other_match_absence cases in test: 0" in p for p in problems)


def test_composition_v3_cup_injury_needs_an_absence_event():
    cases, manifest, v2_ids = v3_set()
    injury = next(c for c in cases if "cup_injury" in c.tags)
    injury.expected_events = []
    assert any(injury.id in p and "cup_injury" in p for p in _problems_v3(cases, manifest, v2_ids))
    for case in cases:
        if "cup_injury" in case.tags:
            case.tags = []
    assert any("cup_injury cases: 0" in p for p in _problems_v3(cases, manifest, v2_ids))


def test_composition_v3_backlog_24_cases_present():
    cases, manifest, v2_ids = v3_set()
    manifest.backlog_cases["24"].append("999")
    assert any("BACKLOG #24 case 999" in p for p in _problems_v3(cases, manifest, v2_ids))
    manifest.backlog_cases["24"] = []
    assert any("backlog_cases 24 is empty" in p for p in _problems_v3(cases, manifest, v2_ids))


def test_composition_v3_every_v2_case_present():
    cases, manifest, v2_ids = v3_set()
    cases = [c for c in cases if c.id != "1000"]
    assert any("v2 cases missing" in p for p in _problems_v3(cases, manifest, v2_ids))


def test_composition_v3_new_case_split_and_author():
    cases, manifest, v2_ids = v3_set()
    new = next(c for c in cases if c.id not in v2_ids and c.split == "test")
    new.split = "dev"
    new.author_handle = "stranger"
    problems = _problems_v3(cases, manifest, v2_ids)
    assert any(new.id in p and "split" in p for p in problems)
    assert any(new.id in p and "watched list" in p for p in problems)


def test_legacy_composition_keeps_four_types():
    cases = compliant_cases()
    assert composition_problems(cases, SNAPSHOT) == []
    problems = composition_problems(cases, SNAPSHOT, ["out", "available"])
    assert any("0 available events" in p for p in problems)
