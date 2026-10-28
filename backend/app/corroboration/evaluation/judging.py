import json
import random
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.errors import AppError
from app.core.eval_results import write_text_atomic
from app.corroboration.evaluation.building import (
    DEV_SHARE,
    MIN_NEW_CONTRADICTS,
    assign_split,
    select_new_cases,
)
from app.corroboration.evaluation.cases import (
    MAX_CASES,
    CandidateCase,
    JudgeCase,
    load_candidates,
    load_cases,
    write_cases,
)
from app.corroboration.evaluation.dataset import (
    CASES_FILE,
    PrelabelEntry,
    Selection,
    SetManifest,
    load_manifest,
    write_manifest,
)
from app.corroboration.evaluation.runner import judge_input
from app.corroboration.judge import JUDGE_PROMPT, PROMPT_VERSION, JudgeOutput, render_input

CALL_OVERHEAD_TOKENS = 3000
CHARS_PER_TOKEN = 4
PROMPT_FILE = "judge-prompt.md"
MAPPING_FILE = "mapping.json"
# The same sentence scripts/session_judge.sh appends to the system prompt for the corroboration
# schema.
REPLY_FORMAT = (
    "Reply with JSON only, no prose and no code fence: "
    '{"label": "supports" | "contradicts" | "related" | "unrelated"}'
)


class JudgingError(AppError):
    pass


class JudgeVerdict(JudgeOutput):
    model_config = {"extra": "forbid", "strict": True}


def _git_commit() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unknown"


def export_inputs(
    *,
    cases: Sequence[JudgeCase],
    candidates: Sequence[CandidateCase],
    out: Path,
    seed: int,
    cases_source: Path | None = None,
    candidates_source: Path | None = None,
) -> int:
    if out.exists() and any(out.iterdir()):
        raise JudgingError(f"{out} is not empty")
    inputs_dir = out / "inputs"
    write_text_atomic(inputs_dir / PROMPT_FILE, JUDGE_PROMPT.text)
    sources: list[tuple[str, str, str | None, Any]] = [
        ("case", case.id, None, case) for case in cases
    ] + [("candidate", cand.id, cand.tier, cand) for cand in candidates]
    random.Random(f"export:{seed}").shuffle(sources)
    mapping: dict[str, Any] = {}
    for number, (kind, source_id, tier, item) in enumerate(sources, start=1):
        input_id = f"I{number:04d}"
        text = render_input(judge_input(item.player, item.anchor, item.post))
        write_text_atomic(inputs_dir / f"{input_id}.md", text)
        mapping[input_id] = {"kind": kind, "id": source_id, "tier": tier}
    write_text_atomic(
        out / MAPPING_FILE,
        json.dumps(
            {
                "seed": seed,
                "prompt_version": PROMPT_VERSION,
                "git_commit": _git_commit(),
                "cases_source": str(cases_source) if cases_source else None,
                "candidates_source": str(candidates_source) if candidates_source else None,
                "inputs": mapping,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    return len(sources)


@dataclass(frozen=True)
class ExportEstimate:
    calls: int
    tokens: int


def estimate_export(out: Path) -> ExportEstimate:
    if not (out / MAPPING_FILE).exists():
        raise JudgingError(f"{out} holds no export (mapping.json is missing)")
    inputs_dir = out / "inputs"
    prompt_chars = len((inputs_dir / PROMPT_FILE).read_text(encoding="utf-8"))
    calls = tokens = 0
    for path in sorted(inputs_dir.glob("*.md")):
        if path.name == PROMPT_FILE:
            continue
        characters = prompt_chars + len(path.read_text(encoding="utf-8")) + len(REPLY_FORMAT)
        calls += 1
        tokens += characters // CHARS_PER_TOKEN + CALL_OVERHEAD_TOKENS
    return ExportEstimate(calls, tokens)


def read_mapping(export: Path) -> dict[str, Any]:
    try:
        return json.loads((export / MAPPING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise JudgingError(f"{export} holds no export (mapping.json is unreadable)") from None


@dataclass
class ImportReport:
    copied: int = 0
    new: int = 0
    candidates: int = 0
    skipped_without_verdict: list[str] = field(default_factory=list)
    skipped_in_set: list[str] = field(default_factory=list)


def import_verdicts(
    export: Path,
    set_dir: Path,
    judge: str,
    *,
    select: int | None = None,
    seed: int = 0,
    force: bool = False,
    now: datetime | None = None,
) -> ImportReport:
    mapping = read_mapping(export)
    report = ImportReport()
    labels: dict[str, tuple[str, dict[str, Any]]] = {}
    invalid: list[str] = []
    missing_cases: list[str] = []
    for input_id, item in mapping["inputs"].items():
        path = export / "verdicts" / f"{input_id}.json"
        if not path.exists():
            if item["kind"] == "case":
                missing_cases.append(f"{input_id} ({item['id']})")
            else:
                report.skipped_without_verdict.append(item["id"])
            continue
        try:
            verdict = JudgeVerdict.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError) as exc:
            invalid.append(f"{input_id}: {type(exc).__name__}")
            continue
        labels[item["id"]] = (verdict.label, item)
    if invalid:
        raise JudgingError("invalid verdicts, nothing imported: " + "; ".join(invalid))
    if missing_cases:
        raise JudgingError("cases without a verdict, nothing imported: " + ", ".join(missing_cases))

    manifest = load_manifest(set_dir) or SetManifest(version=set_dir.name)
    cases_path = set_dir / CASES_FILE
    existing = load_cases(cases_path) if cases_path.exists() else []
    existing_ids = {case.id for case in existing}

    v1_results: list[JudgeCase] = []
    if mapping.get("cases_source"):
        for case in load_cases(Path(mapping["cases_source"])):
            if case.id not in labels:
                continue
            if case.id in existing_ids and not force:
                report.skipped_in_set.append(case.id)
                continue
            label = labels[case.id][0]
            v1_results.append(
                case.model_copy(
                    update={
                        "expected": label,
                        "prelabel": label,
                        "labelled_by": judge,
                        "v1_prelabel": case.expected,
                        "reviewed": False,
                    }
                )
            )
    report.copied = len(v1_results)

    pool: list[JudgeCase] = []
    if mapping.get("candidates_source"):
        for cand in load_candidates(Path(mapping["candidates_source"])):
            if cand.id not in labels:
                continue
            if cand.id in existing_ids and not force:
                report.skipped_in_set.append(cand.id)
                continue
            label = labels[cand.id][0]
            pool.append(
                JudgeCase(
                    id=cand.id,
                    split="test",
                    player=cand.player,
                    anchor=cand.anchor,
                    post=cand.post,
                    expected=label,
                    prelabel=label,
                    labelled_by=judge,
                    reviewed=False,
                    has_player_event=cand.has_player_event,
                )
            )
    report.candidates = len(pool)
    chosen = select_new_cases(pool, select, seed) if select is not None else list(pool)
    new_cases = assign_split(chosen, DEV_SHARE, seed)
    report.new = len(new_cases)

    replaced = {case.id for case in [*v1_results, *new_cases]}
    kept = [case for case in existing if case.id not in replaced]
    result = [*kept, *v1_results, *new_cases]
    if len(result) > MAX_CASES:
        raise JudgingError(
            f"the import would make {len(result)} cases, above the maximum of {MAX_CASES}"
        )
    write_cases(cases_path, result)

    manifest.prelabels.append(
        PrelabelEntry(
            judge=judge,
            prompt_version=mapping.get("prompt_version", PROMPT_VERSION),
            git_commit=mapping.get("git_commit", "unknown"),
            inputs=len(labels),
            imported_at=now or datetime.now(UTC),
        )
    )
    if select is not None:
        manifest.selection = Selection(
            seed=seed, size=select, pool=len(pool), min_contradicts=MIN_NEW_CONTRADICTS
        )
    write_manifest(set_dir, manifest)
    return report
