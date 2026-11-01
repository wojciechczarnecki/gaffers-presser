from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from pydantic import BaseModel, ConfigDict

from app.core.eval_results import write_lines_atomic
from app.extraction.linking import PlayerRecord
from app.extraction.schemas import Certainty, EventType

if TYPE_CHECKING:
    from app.extraction.evaluation.dataset import SetManifest

Tag = Literal[
    "international_injury",
    "national_lineup",
    "womens_lineup",
    "cup_european_lineup",
    "other_match_lineup",
    "other_match_absence",
    "cup_injury",
    "multi_player",
    "ambiguous_name",
    "nickname",
    "accented_name",
]


class ExpectedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention: str
    fpl_id: int | None
    event_type: EventType
    certainty: Certainty


ReviewSource = Literal["owner", "agent", "prelabel"]


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    author_handle: str
    text: str
    created_at: datetime
    is_repost: bool
    is_reply: bool
    split: Literal["dev", "test"]
    synthetic: bool
    reviewed: bool
    review_source: ReviewSource | None = None
    tags: list[Tag]
    expected_events: list[ExpectedEvent]


def parse_cases(text: str) -> list[EvalCase]:
    return [EvalCase.model_validate_json(line) for line in text.splitlines() if line.strip()]


def load_cases(path: Path) -> list[EvalCase]:
    return parse_cases(Path(path).read_text())


def write_cases(path: Path, cases: Sequence[EvalCase]) -> None:
    write_lines_atomic(path, [case.model_dump_json() for case in cases])


MIN_REAL = 100
SYNTHETIC_RANGE = (20, 40)
DEV_SHARE_RANGE = (0.25, 0.35)
MIN_PER_TYPE_IN_TEST = 5
MIN_PER_CATEGORY = 3
EVENT_TYPES: tuple[str, ...] = get_args(EventType)
LEGACY_EVENT_TYPES: tuple[str, ...] = ("out", "doubt", "benched", "confirmed_starter")
CERTAINTIES: tuple[str, ...] = get_args(Certainty)
NO_EVENT_TAGS = ("national_lineup", "womens_lineup", "cup_european_lineup")


def composition_problems(
    cases: Sequence[EvalCase],
    snapshot: Sequence[PlayerRecord],
    event_types: Sequence[str] = LEGACY_EVENT_TYPES,
) -> list[str]:
    problems: list[str] = []

    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        problems.append(f"duplicate ids: {', '.join(duplicates)}")

    real = sum(1 for case in cases if not case.synthetic)
    synthetic = len(cases) - real
    if real < MIN_REAL:
        problems.append(f"real cases: {real}, need at least {MIN_REAL}")
    low, high = SYNTHETIC_RANGE
    if not low <= synthetic <= high:
        problems.append(f"synthetic cases: {synthetic}, need {low}-{high}")

    if cases:
        dev_share = sum(1 for case in cases if case.split == "dev") / len(cases)
        low_share, high_share = DEV_SHARE_RANGE
        if not low_share <= dev_share <= high_share:
            problems.append(f"dev share: {dev_share:.2f}, need {low_share:.2f}-{high_share:.2f}")

    test_events = [e for case in cases if case.split == "test" for e in case.expected_events]
    for event_type in event_types:
        count = sum(1 for e in test_events if e.event_type == event_type)
        if count < MIN_PER_TYPE_IN_TEST:
            problems.append(
                f"test split has {count} {event_type} events, need {MIN_PER_TYPE_IN_TEST}"
            )
    for certainty in CERTAINTIES:
        count = sum(1 for e in test_events if e.certainty == certainty)
        if count < MIN_PER_TYPE_IN_TEST:
            problems.append(
                f"test split has {count} {certainty} events, need {MIN_PER_TYPE_IN_TEST}"
            )

    injuries = [c for c in cases if "international_injury" in c.tags and c.expected_events]
    if len(injuries) < MIN_PER_CATEGORY:
        problems.append(
            f"international_injury cases with events: {len(injuries)}, need {MIN_PER_CATEGORY}"
        )
    for tag in NO_EVENT_TAGS:
        empty = [c for c in cases if tag in c.tags and not c.expected_events]
        if len(empty) < MIN_PER_CATEGORY:
            problems.append(f"{tag} cases with no events: {len(empty)}, need {MIN_PER_CATEGORY}")
        with_events = [c.id for c in cases if tag in c.tags and c.expected_events]
        if with_events:
            problems.append(f"{tag} cases must have no expected events: {', '.join(with_events)}")

    if not any(case.is_repost for case in cases):
        problems.append("no repost case")
    for tag in ("multi_player", "ambiguous_name", "nickname", "accented_name"):
        if not any(tag in case.tags for case in cases):
            problems.append(f"no case tagged {tag}")

    known_ids = {player.fpl_id for player in snapshot}
    for case in cases:
        for event in case.expected_events:
            if event.fpl_id is not None and event.fpl_id not in known_ids:
                problems.append(f"case {case.id}: fpl_id {event.fpl_id} is not in the snapshot")

    return problems


# GW6 opened with the deadline of 2026-10-03T10:00Z and GW7 closed at 2026-10-17T10:00Z; the
# retrieval set v2 windows (evals/retrieval/v2/queries.jsonl) use the same bounds.
NEW_CASES_FROM = datetime(2026, 10, 1, tzinfo=UTC)
NEW_CASES_UNTIL = datetime(2026, 10, 18, tzinfo=UTC)
GW_WINDOW = (datetime(2026, 10, 3, 10, tzinfo=UTC), datetime(2026, 10, 17, 10, tzinfo=UTC))
MIN_NEW_REAL = 70
MIN_WINDOW_LINEUPS = 15
MIN_OTHER_MATCH = 8
MIN_OTHER_MATCH_IN_TEST = 5
LINEUP_TYPES = ("benched", "confirmed_starter")
BACKLOG_23_NO_EVENT = ("2104646876389650860", "2106010889262436608")
BACKLOG_23_NOT_CONFIRMED = "2104158837540622748"


def composition_problems_v2(
    cases: Sequence[EvalCase],
    snapshot: Sequence[PlayerRecord],
    manifest: "SetManifest",
    v1_ids: Collection[str],
) -> list[str]:
    problems = composition_problems(cases, snapshot)
    by_id = {case.id: case for case in cases}

    missing = sorted(set(v1_ids) - set(by_id))
    if missing:
        problems.append(f"v1 cases missing from v2: {', '.join(missing)}")

    new_real = [c for c in cases if c.id not in v1_ids and not c.synthetic]
    in_window = [c for c in new_real if NEW_CASES_FROM <= c.created_at < NEW_CASES_UNTIL]
    if len(in_window) < MIN_NEW_REAL:
        problems.append(f"new real cases in 2026-10-01..17: {len(in_window)}, need {MIN_NEW_REAL}")

    start, end = GW_WINDOW
    window_lineups = [
        e
        for c in cases
        if c.split == "test" and start <= c.created_at < end
        for e in c.expected_events
        if e.event_type in LINEUP_TYPES
    ]
    if len(window_lineups) < MIN_WINDOW_LINEUPS:
        problems.append(
            f"test line-up events in the GW6-GW7 windows: {len(window_lineups)}, "
            f"need {MIN_WINDOW_LINEUPS}"
        )

    other = [c for c in cases if "other_match_lineup" in c.tags]
    if len(other) < MIN_OTHER_MATCH:
        problems.append(f"other_match_lineup cases: {len(other)}, need {MIN_OTHER_MATCH}")
    other_test = [c for c in other if c.split == "test"]
    if len(other_test) < MIN_OTHER_MATCH_IN_TEST:
        problems.append(
            f"other_match_lineup cases in test: {len(other_test)}, need {MIN_OTHER_MATCH_IN_TEST}"
        )
    with_events = [c.id for c in other if c.expected_events]
    if with_events:
        problems.append(
            f"other_match_lineup cases must have no expected events: {', '.join(with_events)}"
        )

    for case_id in BACKLOG_23_NO_EVENT:
        case = by_id.get(case_id)
        if case is None:
            problems.append(f"BACKLOG #23 case {case_id} is missing")
        elif case.expected_events:
            problems.append(f"BACKLOG #23 case {case_id} must have no expected events")
    case = by_id.get(BACKLOG_23_NOT_CONFIRMED)
    if case is None:
        problems.append(f"BACKLOG #23 case {BACKLOG_23_NOT_CONFIRMED} is missing")
    elif any(e.certainty == "confirmed" for e in case.expected_events):
        problems.append(f"BACKLOG #23 case {BACKLOG_23_NOT_CONFIRMED} must have no confirmed event")
    listed_23 = set(manifest.backlog_cases.get("23", []))
    unlisted = sorted({*BACKLOG_23_NO_EVENT, BACKLOG_23_NOT_CONFIRMED} - listed_23)
    if unlisted:
        problems.append(f"set.json backlog_cases 23 lacks: {', '.join(unlisted)}")

    for case_id in manifest.backlog_54_lineups:
        case = by_id.get(case_id)
        if case is None:
            problems.append(f"BACKLOG #54 case {case_id} is missing")
        elif "other_match_lineup" not in case.tags:
            problems.append(f"BACKLOG #54 line-up {case_id} is not tagged other_match_lineup")

    watched = {handle.casefold() for handle in manifest.list_handles}
    for case in new_real:
        expected_split = "dev" if int(case.id) % 10 < 3 else "test"
        if case.split != expected_split:
            problems.append(f"case {case.id}: split {case.split}, the rule gives {expected_split}")
        if case.author_handle.casefold() not in watched:
            problems.append(f"case {case.id}: author is not on the watched list")

    return problems


MIN_OTHER_MATCH_ABSENCE = 8
MIN_OTHER_MATCH_ABSENCE_IN_TEST = 5
MIN_CUP_INJURY = 3
BACKLOG_24 = "24"
ABSENCE_TYPES = ("out", "doubt")


def composition_problems_v3(
    cases: Sequence[EvalCase],
    snapshot: Sequence[PlayerRecord],
    manifest: "SetManifest",
    v2_ids: Collection[str],
) -> list[str]:
    problems = composition_problems(cases, snapshot, EVENT_TYPES)
    by_id = {case.id: case for case in cases}

    missing = sorted(set(v2_ids) - set(by_id))
    if missing:
        problems.append(f"v2 cases missing from v3: {', '.join(missing)}")

    absence = [c for c in cases if "other_match_absence" in c.tags]
    if len(absence) < MIN_OTHER_MATCH_ABSENCE:
        problems.append(
            f"other_match_absence cases: {len(absence)}, need {MIN_OTHER_MATCH_ABSENCE}"
        )
    absence_test = [c for c in absence if c.split == "test"]
    if len(absence_test) < MIN_OTHER_MATCH_ABSENCE_IN_TEST:
        problems.append(
            f"other_match_absence cases in test: {len(absence_test)}, "
            f"need {MIN_OTHER_MATCH_ABSENCE_IN_TEST}"
        )
    for case in absence:
        if any(e.event_type in ABSENCE_TYPES for e in case.expected_events):
            problems.append(f"other_match_absence case {case.id} must have no out or doubt event")

    cup_injury = [c for c in cases if "cup_injury" in c.tags]
    if len(cup_injury) < MIN_CUP_INJURY:
        problems.append(f"cup_injury cases: {len(cup_injury)}, need {MIN_CUP_INJURY}")
    for case in cup_injury:
        if not any(e.event_type in ABSENCE_TYPES for e in case.expected_events):
            problems.append(f"cup_injury case {case.id} needs an out or doubt event")

    with_events = [c.id for c in cases if "other_match_lineup" in c.tags and c.expected_events]
    if with_events:
        problems.append(
            f"other_match_lineup cases must have no expected events: {', '.join(with_events)}"
        )

    listed_24 = manifest.backlog_cases.get(BACKLOG_24, [])
    if not listed_24:
        problems.append(f"set.json backlog_cases {BACKLOG_24} is empty")
    for case_id in listed_24:
        if case_id not in by_id:
            problems.append(f"BACKLOG #24 case {case_id} is missing")

    watched = {handle.casefold() for handle in manifest.list_handles}
    for case in cases:
        if case.id in v2_ids or case.synthetic:
            continue
        expected_split = "dev" if int(case.id) % 10 < 3 else "test"
        if case.split != expected_split:
            problems.append(f"case {case.id}: split {case.split}, the rule gives {expected_split}")
        if case.author_handle.casefold() not in watched:
            problems.append(f"case {case.id}: author is not on the watched list")

    return problems


KROUPI_CASE = "2115825835525488887"
TIPS_CASE = "2116448660980732008"
REST_POST_CASE = "2116079251476611126"
WRONG_TAG_CASE = "2115797596119740910"
SPEC_022_CASES = (
    KROUPI_CASE,
    "2116174660693836212",
    "2116188146660409531",
    "2116265028458315935",
    TIPS_CASE,
    REST_POST_CASE,
)


def composition_problems_v4(
    cases: Sequence[EvalCase],
    snapshot: Sequence[PlayerRecord],
    manifest: "SetManifest",
    v2_ids: Collection[str],
    v3_ids: Collection[str],
) -> list[str]:
    problems = composition_problems_v3(cases, snapshot, manifest, v2_ids)
    by_id = {case.id: case for case in cases}

    missing = sorted(set(v3_ids) - set(by_id))
    if missing:
        problems.append(f"v3 cases missing from v4: {', '.join(missing)}")

    for case_id in SPEC_022_CASES:
        if case_id not in by_id:
            problems.append(f"spec 022 case {case_id} is missing")

    kroupi = by_id.get(KROUPI_CASE)
    if kroupi is not None:
        events = [e for e in kroupi.expected_events if "kroupi" in e.mention.casefold()]
        if not any(e.event_type == "benched" for e in events):
            problems.append(f"case {KROUPI_CASE} needs a benched event for Kroupi")
        if any(e.event_type in ABSENCE_TYPES for e in events):
            problems.append(f"case {KROUPI_CASE} must have no out or doubt event for Kroupi")

    tips = by_id.get(TIPS_CASE)
    if tips is not None and tips.expected_events:
        problems.append(f"case {TIPS_CASE} must have no expected events")

    rest = by_id.get(REST_POST_CASE)
    if rest is not None:
        if rest.split != "test":
            problems.append(f"case {REST_POST_CASE} must stay in the test split")
        if any(e.event_type in ABSENCE_TYPES for e in rest.expected_events):
            problems.append(f"case {REST_POST_CASE} must have no out or doubt event")

    wrong = by_id.get(WRONG_TAG_CASE)
    if wrong is not None and "other_match_absence" in wrong.tags:
        problems.append(f"case {WRONG_TAG_CASE} must not carry the other_match_absence tag")

    return problems
