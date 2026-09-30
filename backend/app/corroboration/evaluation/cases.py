import os
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from app.core.local_time import format_local
from app.corroboration.schemas import Label
from app.llm.chat import DEFAULT_MODEL

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals" / "corroboration"
DEFAULT_CASES_PATH = EVALS_DIR / "v1" / "cases.jsonl"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"

# A tier above the flash class and from another vendor than the default judge, so its
# pre-labels do not share the judge's biases.
PRELABEL_MODEL = "anthropic/claude-haiku-4.5"

LABELS: tuple[str, ...] = get_args(Label)

MIN_CASES = 50
MAX_CASES = 70
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


def parse_cases(text: str) -> list[JudgeCase]:
    return [JudgeCase.model_validate_json(line) for line in text.splitlines() if line.strip()]


def load_cases(path: Path) -> list[JudgeCase]:
    return parse_cases(Path(path).read_text())


def write_cases(path: Path, cases: Sequence[JudgeCase]) -> None:
    # Atomic: a temporary file next to the target, then os.replace, so an interrupted
    # write never leaves a half-written set behind.
    path = Path(path)
    lines = [case.model_dump_json() for case in cases]
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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

    same_as_judge = sorted({case.id for case in cases if case.labelled_by == DEFAULT_MODEL})
    if same_as_judge:
        problems.append(f"pre-labelled by the judge's own model: {', '.join(same_as_judge)}")

    if not any(is_known_miss(case) for case in cases):
        problems.append("no known-miss case (a post with no event for the player, not unrelated)")
    return problems


RULE = "─" * 72


def _who(author: str, original: str | None) -> str:
    return f"@{author} (repost of @{original})" if original else f"@{author}"


def render_case(case: JudgeCase, progress: str) -> str:
    anchor, post, player = case.anchor, case.post, case.player
    team = f" ({player.team})" if player.team else ""
    lines = [
        RULE,
        progress,
        f"id: {case.id}  split: {case.split}  reviewed: {'yes' if case.reviewed else 'no'}",
        f"player: {player.web_name}{team}, FPL ID {player.fpl_id}"
        f"  extraction event for the player: {'yes' if case.has_player_event else 'no'}",
        f"anchor: {anchor.event_type} ({anchor.certainty}) by @{anchor.author_handle}"
        f" at {format_local(anchor.created_at)}",
        f"    {' '.join(anchor.text.split())}",
        f"post: {_who(post.author_handle, post.reposted_author_handle)}"
        f" at {format_local(post.created_at)}",
        f"    {' '.join(post.text.split())}",
        f"pre-label: {case.expected}  (by {case.labelled_by})",
    ]
    return "\n".join(lines)
