import json
import random
import re
import subprocess
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.content import load_prompt
from app.core.errors import AppError
from app.core.eval_results import write_text_atomic
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases
from app.extraction.evaluation.compare import compare_events
from app.extraction.evaluation.dataset import (
    CASES_FILE,
    PrelabelEntry,
    SetManifest,
    load_manifest,
    write_manifest,
)
from app.extraction.fixtures import FixtureIndex, render_fixtures_context
from app.extraction.flow import _render_post
from app.extraction.linking import PlayerIndex
from app.extraction.schemas import ExtractedEvent, PostInput

CALL_OVERHEAD_TOKENS = 3000
CHARS_PER_TOKEN = 4
PROMPT_FILE = "judge-prompt.md"
MAPPING_FILE = "mapping.json"
MARKER = "other_match_lineup"
FLAG_TAGS = (MARKER, "other_match_absence", "cup_injury")
LINEUP_TYPES = ("benched", "confirmed_starter")
LINEUP_KEYWORDS = re.compile(
    r"\bXI\b|line-?ups?\b|\bstarting\b|\bstarts?\b|\bbench(?:ed)?\b|\bsubs\b|team news",
    re.IGNORECASE,
)
AVAILABILITY_KEYWORDS = re.compile(
    r"\b(?:fit|fitness|trained|training|available|suspen\w*|ban(?:ned)?|rest(?:ed)?|rotat\w*"
    r"|squad|injur\w*|knock|miss(?:es)?)\b",
    re.IGNORECASE,
)
DEFAULT_QUOTAS = {
    "other": 25,
    "absence": 15,
    "available": 15,
    "lineup": 35,
    "event": 10,
    "none": 10,
}
# The same sentence scripts/session_judge.sh appends to the system prompt for the extraction schema.
REPLY_FORMAT = (
    "Reply with JSON only, no prose and no code fence: "
    '{"events": [{"player": "...", "team": "..." | null, "event_type": "out" | "doubt" | '
    '"benched" | "confirmed_starter" | "available", "certainty": "confirmed" | "likely" | '
    '"rumour"}], "other_match_lineup": true | false, "other_match_absence": true | false, '
    '"cup_injury": true | false}'
)


class JudgingError(AppError):
    pass


class CorpusRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x_id: int
    author_handle: str
    text: str
    created_at: datetime
    is_repost: bool
    is_reply: bool
    is_source: bool | None = None


class StrictEvent(ExtractedEvent):
    model_config = ConfigDict(extra="forbid", strict=True)

    player: str = Field(min_length=1)


class PrelabelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    events: list[StrictEvent]
    other_match_lineup: bool
    other_match_absence: bool = False
    cup_injury: bool = False

    def flag_tags(self) -> list[str]:
        flags = (self.other_match_lineup, self.other_match_absence, self.cup_injury)
        return [tag for tag, on in zip(FLAG_TAGS, flags, strict=True) if on]


@dataclass(frozen=True)
class JudgeSource:
    kind: str  # case | post
    post: PostInput
    case_id: str | None = None
    required: bool = False


def read_corpus(path: Path) -> list[CorpusRow]:
    rows = [
        CorpusRow.model_validate_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return rows


def sources_from_cases(cases: Sequence[EvalCase]) -> list[JudgeSource]:
    return [
        JudgeSource(
            kind="case",
            case_id=case.id,
            post=PostInput(
                x_id=0,
                author_handle=case.author_handle,
                text=case.text,
                created_at=case.created_at,
                is_repost=case.is_repost,
                is_reply=case.is_reply,
            ),
        )
        for case in cases
    ]


def sources_from_corpus(
    rows: Sequence[CorpusRow],
    *,
    since: datetime | None,
    until: datetime | None,
    keywords: bool,
    rest_sample: int,
    seed: int,
    x_ids: Collection[int],
    exclude: Collection[str],
    authors: Collection[str] | None = None,
    availability_keywords: bool = False,
) -> list[JudgeSource]:
    wanted = set(x_ids)
    watched = {a.casefold() for a in authors} if authors is not None else None
    pool = [
        row
        for row in rows
        if str(row.x_id) not in exclude
        and (watched is None or row.author_handle.casefold() in watched)
        and (
            row.x_id in wanted
            or (
                row.is_source
                and (since is None or row.created_at >= since)
                and (until is None or row.created_at < until)
            )
        )
    ]
    if keywords or availability_keywords:
        matching = [
            row
            for row in pool
            if row.x_id in wanted
            or (keywords and LINEUP_KEYWORDS.search(row.text))
            or (availability_keywords and AVAILABILITY_KEYWORDS.search(row.text))
        ]
        rest = [row for row in pool if row not in matching]
        sampled = random.Random(f"rest:{seed}").sample(rest, min(rest_sample, len(rest)))
        pool = [row for row in pool if row in matching or row in sampled]
    return [
        JudgeSource(
            kind="post",
            required=row.x_id in wanted,
            post=PostInput(
                x_id=row.x_id,
                author_handle=row.author_handle,
                text=row.text,
                created_at=row.created_at,
                is_repost=row.is_repost,
                is_reply=row.is_reply,
            ),
        )
        for row in pool
    ]


def render_input(post: PostInput, fixtures: FixtureIndex, index: PlayerIndex) -> str:
    return "\n".join(
        [
            "# Post",
            _render_post(post),
            "",
            "# Premier League fixtures around the time of the post",
            render_fixtures_context(fixtures, index, post.created_at),
            "",
        ]
    )


def judge_prompt() -> tuple[str, dict[str, int]]:
    extraction = load_prompt("extraction")
    prelabel = load_prompt("extraction_prelabel")
    versions = {"extraction": extraction.version, "extraction_prelabel": prelabel.version}
    return f"{extraction.text.rstrip()}\n\n{prelabel.text}", versions


def _git_commit() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unknown"


def export_inputs(
    sources: Sequence[JudgeSource],
    fixtures: FixtureIndex,
    index: PlayerIndex,
    out: Path,
    *,
    seed: int,
    cases_source: Path | None = None,
) -> int:
    if out.exists() and any(out.iterdir()):
        raise JudgingError(f"{out} is not empty")
    prompt, versions = judge_prompt()
    inputs_dir = out / "inputs"
    write_text_atomic(inputs_dir / PROMPT_FILE, prompt)
    order = list(sources)
    random.Random(f"export:{seed}").shuffle(order)
    mapping: dict[str, Any] = {}
    for number, source in enumerate(order, start=1):
        input_id = f"I{number:04d}"
        write_text_atomic(inputs_dir / f"{input_id}.md", render_input(source.post, fixtures, index))
        item: dict[str, Any] = {"kind": source.kind, "required": source.required}
        if source.kind == "case":
            item["case_id"] = source.case_id
        else:
            item["post"] = json.loads(
                json.dumps(
                    {
                        "x_id": source.post.x_id,
                        "author_handle": source.post.author_handle,
                        "text": source.post.text,
                        "created_at": source.post.created_at.isoformat(),
                        "is_repost": source.post.is_repost,
                        "is_reply": source.post.is_reply,
                    }
                )
            )
        mapping[input_id] = item
    write_text_atomic(
        out / MAPPING_FILE,
        json.dumps(
            {
                "seed": seed,
                "prompts": versions,
                "git_commit": _git_commit(),
                "cases_source": str(cases_source) if cases_source is not None else None,
                "inputs": mapping,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    return len(order)


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
    changed: int = 0
    new: int = 0
    candidates: int = 0
    skipped_without_verdict: list[str] = field(default_factory=list)
    skipped_in_set: list[str] = field(default_factory=list)
    skipped_unwatched: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Judged:
    input_id: str
    item: dict[str, Any]
    events: list[ExpectedEvent]
    tags: tuple[str, ...]

    @property
    def flag(self) -> bool:
        return MARKER in self.tags


def parse_quotas(text: str) -> dict[str, int]:
    quotas = dict(DEFAULT_QUOTAS)
    for part in filter(None, (piece.strip() for piece in text.split(","))):
        name, _, value = part.partition("=")
        if name not in quotas or not value.isdigit():
            raise JudgingError(
                f"bad quota {part!r}; use other=, absence=, available=, lineup=, event=, none="
            )
        quotas[name] = int(value)
    return quotas


def _link(events: Sequence[StrictEvent], index: PlayerIndex) -> list[ExpectedEvent]:
    linked = []
    for event in events:
        candidates = index.resolve(event.player, event.team)
        linked.append(
            ExpectedEvent(
                mention=event.player,
                fpl_id=candidates[0].fpl_id if len(candidates) == 1 else None,
                event_type=event.event_type,
                certainty=event.certainty,
            )
        )
    return linked


def _carry_v1_links(judged: list[ExpectedEvent], v1: Sequence[ExpectedEvent]) -> None:
    # An ambiguous name the import cannot link (it makes no LLM call) takes the v1 event's id,
    # so a linking gap does not pose as a label change.
    for position, event in enumerate(judged):
        if event.fpl_id is not None:
            continue
        for other in v1:
            if other.fpl_id is None:
                continue
            _, _, changes = compare_events("", [event], [other])
            if changes:
                judged[position] = event.model_copy(update={"fpl_id": other.fpl_id})
                break


def _tier(events: Sequence[ExpectedEvent], flag: bool, absence: bool = False) -> str:
    if flag:
        return "other"
    if absence:
        return "absence"
    if any(e.event_type == "available" for e in events):
        return "available"
    if any(e.event_type in LINEUP_TYPES for e in events):
        return "lineup"
    return "event" if events else "none"


def select_candidates(
    candidates: Sequence[_Judged], count: int | None, seed: int, quotas: dict[str, int]
) -> list[_Judged]:
    if count is None:
        return list(candidates)
    chosen = [c for c in candidates if c.item.get("required")]
    rest = [c for c in candidates if not c.item.get("required")]
    rng = random.Random(f"select:{seed}")
    tiers: dict[str, list[_Judged]] = {name: [] for name in DEFAULT_QUOTAS}
    for candidate in rest:
        absence = "other_match_absence" in candidate.tags or "cup_injury" in candidate.tags
        tiers[_tier(candidate.events, candidate.flag, absence)].append(candidate)
    for tier in tiers.values():
        rng.shuffle(tier)
    capacity = max(count - len(chosen), 0)
    for name, tier in tiers.items():
        take = min(quotas[name], len(tier), capacity)
        chosen += tier[:take]
        tiers[name] = tier[take:]
        capacity -= take
    for tier in tiers.values():
        take = min(len(tier), capacity)
        chosen += tier[:take]
        capacity -= take
    return chosen


def import_verdicts(
    export: Path,
    set_dir: Path,
    index: PlayerIndex,
    judge: str,
    *,
    select: int | None = None,
    seed: int = 0,
    quotas: dict[str, int] | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> ImportReport:
    mapping = read_mapping(export)
    report = ImportReport()
    judged: list[_Judged] = []
    invalid: list[str] = []
    missing_cases: list[str] = []
    for input_id, item in mapping["inputs"].items():
        path = export / "verdicts" / f"{input_id}.json"
        if not path.exists():
            if item["kind"] == "case":
                missing_cases.append(f"{input_id} ({item['case_id']})")
            else:
                report.skipped_without_verdict.append(input_id)
            continue
        try:
            verdict = PrelabelVerdict.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, ValueError) as exc:
            invalid.append(f"{input_id}: {type(exc).__name__}")
            continue
        judged.append(
            _Judged(input_id, item, _link(verdict.events, index), tuple(verdict.flag_tags()))
        )
    if invalid:
        raise JudgingError("invalid verdicts, nothing imported: " + "; ".join(invalid))
    if missing_cases:
        raise JudgingError("cases without a verdict, nothing imported: " + ", ".join(missing_cases))

    manifest = load_manifest(set_dir) or SetManifest(version=set_dir.name)
    watched = {h.casefold() for h in manifest.list_handles}
    cases_path = set_dir / CASES_FILE
    existing = load_cases(cases_path) if cases_path.exists() else []
    existing_ids = {case.id for case in existing}
    baseline: dict[str, EvalCase] = {}
    if mapping.get("cases_source"):
        baseline = {case.id: case for case in load_cases(Path(mapping["cases_source"]))}

    case_results: list[EvalCase] = []
    candidates: list[_Judged] = []
    for entry in judged:
        if entry.item["kind"] == "case":
            case_id = entry.item["case_id"]
            if case_id in existing_ids and not force:
                report.skipped_in_set.append(case_id)
                continue
            v1 = baseline[case_id]
            events = list(entry.events)
            _carry_v1_links(events, v1.expected_events)
            added, removed, relabelled = compare_events(case_id, events, v1.expected_events)
            tags_same = [t for t in FLAG_TAGS if t in v1.tags] == list(entry.tags)
            if not (added or removed or any(c.fields for c in relabelled)) and tags_same:
                case_results.append(v1)
                report.copied += 1
                continue
            tags = [t for t in v1.tags if t not in FLAG_TAGS] + list(entry.tags)
            case_results.append(
                v1.model_copy(update={"expected_events": events, "tags": tags, "reviewed": False})
            )
            report.changed += 1
        else:
            if watched and entry.item["post"]["author_handle"].casefold() not in watched:
                report.skipped_unwatched.append(str(entry.item["post"]["x_id"]))
                continue
            if str(entry.item["post"]["x_id"]) in existing_ids and not force:
                report.skipped_in_set.append(str(entry.item["post"]["x_id"]))
                continue
            candidates.append(entry)
    report.candidates = len(candidates)

    new_posts = []
    for entry in select_candidates(candidates, select, seed, quotas or DEFAULT_QUOTAS):
        post = entry.item["post"]
        new_posts.append(
            EvalCase(
                id=str(post["x_id"]),
                author_handle=post["author_handle"],
                text=post["text"],
                created_at=datetime.fromisoformat(post["created_at"]),
                is_repost=post["is_repost"],
                is_reply=post["is_reply"],
                split="dev" if post["x_id"] % 10 < 3 else "test",
                synthetic=False,
                reviewed=False,
                tags=list(entry.tags),
                expected_events=entry.events,
            )
        )
    report.new = len(new_posts)

    replaced = {case.id for case in [*case_results, *new_posts]}
    kept = [case for case in existing if case.id not in replaced]
    write_cases(cases_path, [*kept, *case_results, *new_posts])

    versions = mapping.get("prompts") or {}
    manifest.prelabels.append(
        PrelabelEntry(
            judge=judge,
            prompt_versions="+".join(f"{name}@{version}" for name, version in versions.items()),
            git_commit=mapping.get("git_commit", "unknown"),
            inputs=len(judged),
            imported_at=now or datetime.now(UTC),
        )
    )
    write_manifest(set_dir, manifest)
    return report
