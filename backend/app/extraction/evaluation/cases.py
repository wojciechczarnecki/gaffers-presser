from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from app.extraction.linking import PlayerRecord
from app.extraction.schemas import Certainty, EventType

Tag = Literal[
    "international_injury",
    "national_lineup",
    "womens_lineup",
    "cup_european_lineup",
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
    tags: list[Tag]
    expected_events: list[ExpectedEvent]


def load_cases(path: Path) -> list[EvalCase]:
    lines = Path(path).read_text().splitlines()
    return [EvalCase.model_validate_json(line) for line in lines if line.strip()]


def write_cases(path: Path, cases: Sequence[EvalCase]) -> None:
    lines = [case.model_dump_json() for case in cases]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""))


MIN_REAL = 100
SYNTHETIC_RANGE = (20, 40)
DEV_SHARE_RANGE = (0.25, 0.35)
MIN_PER_TYPE_IN_TEST = 5
MIN_PER_CATEGORY = 3
EVENT_TYPES: tuple[str, ...] = get_args(EventType)
CERTAINTIES: tuple[str, ...] = get_args(Certainty)
NO_EVENT_TAGS = ("national_lineup", "womens_lineup", "cup_european_lineup")


def composition_problems(cases: Sequence[EvalCase], snapshot: Sequence[PlayerRecord]) -> list[str]:
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
    for event_type in EVENT_TYPES:
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
