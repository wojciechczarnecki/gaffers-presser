import textwrap
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from app.core.eval_results import write_lines_atomic
from app.core.local_time import format_local
from app.corroboration.config import DEFAULT_JUDGE_MODEL
from app.corroboration.schemas import Label

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals" / "corroboration"
V1_CASES_PATH = EVALS_DIR / "v1" / "cases.jsonl"
V2_SET_DIR = EVALS_DIR / "v2"
DEFAULT_CASES_PATH = V2_SET_DIR / "cases.jsonl"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"

# A tier above the flash class and from another vendor than the default judge, so its
# pre-labels do not share the judge's biases.
PRELABEL_MODEL = "anthropic/claude-haiku-4.5"

LABELS: tuple[str, ...] = get_args(Label)

MIN_CASES = 90
MAX_CASES = 130
MIN_CONTRADICTS = 8
MIN_TEST_CONTRADICTS = 5
DEV_SHARE_RANGE = (0.25, 0.35)


class CasePlayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fpl_id: int
    web_name: str
    team: str | None


class CaseAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_id: int
    author_handle: str
    created_at: datetime
    event_type: str
    certainty: str
    text: str


class CasePost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_id: int
    author_handle: str
    reposted_author_handle: str | None
    is_repost: bool
    created_at: datetime
    text: str


class JudgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    split: Literal["dev", "test"]
    player: CasePlayer
    anchor: CaseAnchor
    post: CasePost
    expected: Label
    labelled_by: str
    reviewed: bool
    has_player_event: bool
    prelabel: Label | None = None
    v1_prelabel: Label | None = None


class CandidateCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    player: CasePlayer
    anchor: CaseAnchor
    post: CasePost
    has_player_event: bool
    tier: str


def parse_cases(text: str) -> list[JudgeCase]:
    return [JudgeCase.model_validate_json(line) for line in text.splitlines() if line.strip()]


def load_cases(path: Path) -> list[JudgeCase]:
    return parse_cases(Path(path).read_text())


def load_candidates(path: Path) -> list[CandidateCase]:
    return [
        CandidateCase.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def write_candidates(path: Path, candidates: Sequence[CandidateCase]) -> None:
    write_lines_atomic(path, [candidate.model_dump_json() for candidate in candidates])


def write_cases(path: Path, cases: Sequence[JudgeCase]) -> None:
    write_lines_atomic(path, [case.model_dump_json() for case in cases])


def is_known_miss(case: JudgeCase) -> bool:
    return not case.has_player_event and case.expected != "unrelated"


def composition_problems(cases: Sequence[JudgeCase]) -> list[str]:
    problems: list[str] = []
    if not MIN_CASES <= len(cases) <= MAX_CASES:
        problems.append(f"cases: {len(cases)}, need {MIN_CASES}-{MAX_CASES}")

    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        problems.append(f"duplicate ids: {', '.join(duplicates)}")

    if cases:
        dev_share = sum(1 for case in cases if case.split == "dev") / len(cases)
        low, high = DEV_SHARE_RANGE
        if not low <= dev_share <= high:
            problems.append(f"dev share: {dev_share:.2f}, need {low:.2f}-{high:.2f}")

    overall = Counter(case.expected for case in cases)
    in_test = Counter(case.expected for case in cases if case.split == "test")
    for label in LABELS:
        if overall[label] == 0:
            problems.append(f"no case with the label {label}")
        if in_test[label] == 0:
            problems.append(f"the test split has no case with the label {label}")

    if overall["contradicts"] < MIN_CONTRADICTS:
        problems.append(
            f"contradicts cases: {overall['contradicts']}, need at least {MIN_CONTRADICTS}"
        )
    if in_test["contradicts"] < MIN_TEST_CONTRADICTS:
        problems.append(
            f"contradicts cases in the test split: {in_test['contradicts']},"
            f" need at least {MIN_TEST_CONTRADICTS}"
        )

    same_as_judge = sorted({case.id for case in cases if case.labelled_by == DEFAULT_JUDGE_MODEL})
    if same_as_judge:
        problems.append(f"pre-labelled by the judge's own model: {', '.join(same_as_judge)}")

    if not any(is_known_miss(case) for case in cases):
        problems.append("no known-miss case (a post with no event for the player, not unrelated)")
    return problems


EVENT_WORDS = {
    "out": "OUT (injury, illness or suspension: will not play)",
    "doubt": "a DOUBT (availability uncertain)",
    "benched": "BENCHED (fit but not in the starting line-up)",
    "confirmed_starter": "a STARTER (will start or is in the confirmed line-up)",
    "available": "FIT or AVAILABLE (no claim about starting)",
}

# One row per anchor event type: what the post says -> the label it earns.
LABEL_TABLE: dict[str, tuple[tuple[str, str], ...]] = {
    "out": (
        ("out", "supports"),
        ("doubt / benched", "related"),
        ("starts or is fit", "contradicts"),
    ),
    "doubt": (
        ("doubt", "supports"),
        ("out / benched", "related"),
        ("starts or is fit", "contradicts"),
    ),
    "benched": (
        ("benched", "supports"),
        ("out / doubt", "related"),
        ("starts or is fit", "contradicts"),
    ),
    "confirmed_starter": (
        ("starts", "supports"),
        ("out / doubt / benched", "contradicts"),
    ),
    "available": (
        ("fit / available", "supports"),
        ("out / doubt", "contradicts"),
        ("starts / benched", "related"),
    ),
}
LABEL_RULES = (
    "Line-up news counts only for the next Premier League match (a national team, cup,"
    " European or women's match is unrelated).",
    "Only the named player counts: a post about someone else is unrelated.",
    "Another topic, or the player only in passing, is unrelated.",
)
ANCHOR_TEXT_LIMIT = 220
DEFAULT_WIDTH = 80


def _who(author: str, original: str | None) -> str:
    return f"@{author} (repost of @{original})" if original else f"@{author}"


def _wrap(text: str, width: int, indent: str = "    ") -> str:
    return textwrap.fill(
        " ".join(text.split()),
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def label_table_row(event_type: str, width: int = DEFAULT_WIDTH) -> str:
    row = LABEL_TABLE.get(event_type, ())
    body = "; ".join(f"post says {says} -> {label}" for says, label in row)
    return textwrap.fill(
        f"If the anchor is {event_type}: {body}",
        width=width,
        subsequent_indent="    ",
    )


def render_case(case: JudgeCase, progress: str, width: int = DEFAULT_WIDTH) -> str:
    anchor, post, player = case.anchor, case.post, case.player
    rule = "─" * width
    team = f" ({player.team})" if player.team else ""
    anchor_text = " ".join(anchor.text.split())
    if len(anchor_text) > ANCHOR_TEXT_LIMIT:
        anchor_text = anchor_text[: ANCHOR_TEXT_LIMIT - 3].rstrip() + "..."
    event = EVENT_WORDS.get(anchor.event_type, anchor.event_type)
    lines = [
        rule,
        progress,
        f"id: {case.id}  split: {case.split}  reviewed: {'yes' if case.reviewed else 'no'}",
        "",
        textwrap.fill(f"LABEL THIS POST — what does it say about {player.web_name}{team}?", width),
        textwrap.fill(
            f"{_who(post.author_handle, post.reposted_author_handle)}"
            f" at {format_local(post.created_at)}",
            width,
        ),
        "",
        _wrap(post.text, width),
        "",
        textwrap.fill(f"COMPARE WITH (anchor): {player.web_name} is {event}", width),
        textwrap.fill(
            f"by @{anchor.author_handle} at {format_local(anchor.created_at)},"
            f" certainty {anchor.certainty}",
            width,
        ),
        "",
        _wrap(anchor_text, width),
        "",
        "HOW TO LABEL",
        label_table_row(anchor.event_type, width),
        *(
            textwrap.fill(f"- {rule_text}", width, subsequent_indent="  ")
            for rule_text in LABEL_RULES
        ),
        "",
        *prelabel_lines(case),
    ]
    return "\n".join(lines)


def session_label(case: JudgeCase) -> Label:
    return case.prelabel or case.expected


def prelabels_disagree(case: JudgeCase) -> bool:
    return case.v1_prelabel is not None and case.v1_prelabel != session_label(case)


def prelabel_lines(case: JudgeCase) -> list[str]:
    lines = [f"session pre-label: {session_label(case)}  (by {case.labelled_by})"]
    if case.v1_prelabel is not None:
        lines.append(f"haiku pre-label: {case.v1_prelabel}  (by {PRELABEL_MODEL})")
    if prelabels_disagree(case):
        lines.append("DISAGREE: the two pre-labels differ, pick with 1-4")
    return lines


def review_order(cases: Sequence[JudgeCase], positions: Sequence[int]) -> list[int]:
    def group(i: int) -> int:
        case = cases[i]
        if case.v1_prelabel is None:
            return 2
        if case.prelabel is not None and case.prelabel != case.v1_prelabel:
            return 0
        return 1

    return sorted(positions, key=lambda i: (group(i), i))


@dataclass(frozen=True)
class Agreement:
    reviewed: int
    by_judge: dict[str, tuple[int, int]]
    v1: tuple[int, int]
    differing: list[str]


def prelabel_agreement(cases: Sequence[JudgeCase]) -> Agreement:
    reviewed = [case for case in cases if case.reviewed]
    by_judge: dict[str, list[int]] = {}
    differing: list[str] = []
    for case in reviewed:
        if case.prelabel is None:
            continue
        counts = by_judge.setdefault(case.labelled_by, [0, 0])
        counts[0] += 1
        if case.expected != case.prelabel:
            counts[1] += 1
            differing.append(
                f"{case.id}: owner {case.expected}, session {case.prelabel}"
                + (f", haiku {case.v1_prelabel}" if case.v1_prelabel else "")
            )
    v1 = [case for case in reviewed if case.v1_prelabel is not None]
    return Agreement(
        len(reviewed),
        {judge: (counts[0], counts[1]) for judge, counts in by_judge.items()},
        (len(v1), sum(1 for case in v1 if case.expected != case.v1_prelabel)),
        differing,
    )
