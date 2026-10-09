import os
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.presser.facts import FactSheet, check_fact_sheet
from app.presser.writer import PreviousPresser

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals" / "presser"
DEFAULT_CASES_PATH = EVALS_DIR / "v2" / "cases.jsonl"
HISTORY_PATH = EVALS_DIR / "v2" / "history.jsonl"
PSEUDONYMS_PATH = EVALS_DIR / "v2" / "pseudonyms.toml"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"

MIN_CASES = 14
MAX_CASES = 18
REAL_CASES = 10
MIN_SYNTHETIC_CASES = 5
MIN_TEST_CASES = 8
MAX_PREVIOUS = 2
EDGE_TAGS = ("tie_win", "low_captain", "all_negative", "chip_flop", "first_gameweek", "no_team")


class PresserCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    split: Literal["dev", "test"]
    source: Literal["real", "synthetic"]
    tags: list[str] = Field(default_factory=list)
    facts: FactSheet
    previous: list[PreviousPresser] = Field(default_factory=list, max_length=MAX_PREVIOUS)


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    league: str
    gameweek: int
    text: str


def parse_cases(text: str) -> list[PresserCase]:
    return [PresserCase.model_validate_json(line) for line in text.splitlines() if line.strip()]


def load_cases(path: Path) -> list[PresserCase]:
    return parse_cases(Path(path).read_text(encoding="utf-8"))


def _write_lines(path: Path, lines: list[str]) -> None:
    # Atomic: a temporary file next to the target, then os.replace, so an interrupted
    # write never leaves a half-written set behind.
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_cases(path: Path, cases: Sequence[PresserCase]) -> None:
    _write_lines(path, [case.model_dump_json() for case in cases])


def load_history(path: Path = HISTORY_PATH) -> list[HistoryEntry]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [HistoryEntry.model_validate_json(line) for line in lines if line.strip()]


def write_history(path: Path, entries: Sequence[HistoryEntry]) -> None:
    _write_lines(path, [entry.model_dump_json() for entry in entries])


def composition_problems(cases: Sequence[PresserCase]) -> list[str]:
    problems: list[str] = []
    if not MIN_CASES <= len(cases) <= MAX_CASES:
        problems.append(f"cases: {len(cases)}, need {MIN_CASES}-{MAX_CASES}")
    real = sum(case.source == "real" for case in cases)
    if real != REAL_CASES:
        problems.append(f"real cases: {real}, need {REAL_CASES}")
    synthetic = sum(case.source == "synthetic" for case in cases)
    if synthetic < MIN_SYNTHETIC_CASES:
        problems.append(f"synthetic cases: {synthetic}, need at least {MIN_SYNTHETIC_CASES}")

    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        problems.append(f"duplicate ids: {', '.join(duplicates)}")

    by_split = Counter((case.split, case.source) for case in cases)
    for split in ("dev", "test"):
        for source in ("real", "synthetic"):
            if by_split[(split, source)] == 0:
                problems.append(f"the {split} split has no {source} case")
    in_test = sum(case.split == "test" for case in cases)
    if in_test < MIN_TEST_CASES:
        problems.append(f"test split: {in_test} cases, need at least {MIN_TEST_CASES}")

    for case in cases:
        if check_fact_sheet(case.facts):
            problems.append(f"{case.id}: the fact sheet is inconsistent")
        if any(entry.gameweek >= case.facts.gameweek for entry in case.previous):
            problems.append(f"{case.id}: a previous presser is not from an earlier gameweek")

    tags = {tag for case in cases for tag in case.tags}
    for tag in EDGE_TAGS:
        if tag not in tags:
            problems.append(f"no case tagged {tag}")
    return problems
