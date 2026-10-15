import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.content import load_prompt
from app.core.eval_results import write_text_atomic
from app.core.local_time import format_local
from app.retrieval.evaluation.dataset import CorpusPost, EvaluationError, Judgement, PlayerQuery
from app.retrieval.evaluation.labels import OWNER, AnyQuery
from app.retrieval.evaluation.pooling import new_posts
from app.retrieval.evaluation.runner import RELEVANCE_PROMPTS

CALL_OVERHEAD_TOKENS = 3000
CHARS_PER_TOKEN = 4
DEFAULT_MAX_POSTS = 30
PROMPT_FILE = "judge-prompt.md"
MAPPING_FILE = "mapping.json"
PROMPT_NAMES = RELEVANCE_PROMPTS
# The same sentence scripts/session_judge.sh appends to the system prompt for the retrieval schema.
REPLY_FORMAT = (
    'Reply with JSON only, no prose and no code fence: {"labels": {"P1": true, "P2": false}}'
    " with exactly one boolean for every post ID listed in the input."
)


@dataclass(frozen=True)
class ExportedInput:
    input_id: str
    query_id: str
    slice: str
    posts: dict[str, int]


@dataclass
class ExportSummary:
    inputs: list[ExportedInput] = field(default_factory=list)
    queries_without_new_posts: int = 0

    @property
    def posts(self) -> int:
        return sum(len(item.posts) for item in self.inputs)


def slice_of(query: AnyQuery) -> str:
    return "player" if isinstance(query, PlayerQuery) else "free"


def _when(moment: datetime) -> str:
    return format_local(moment)


def render_input(query: AnyQuery, posts: Sequence[CorpusPost]) -> str:
    if isinstance(query, PlayerQuery):
        player = query.player
        lines = [
            "# Player",
            f"{player.first_name} {player.second_name} ({player.web_name}), {player.team}",
            f"Gameweek {query.gameweek}, deadline {_when(query.deadline_at)} (Europe/Warsaw)",
            f"Window searched: {_when(query.window_start)} to {_when(query.window_end)}"
            " (Europe/Warsaw)",
        ]
    else:
        lines = ["# Query", query.text]
    lines += ["", "# Posts"]
    for number, post in enumerate(posts, start=1):
        # Every body line is indented, so no post text can start a line with a "[P<n>]" ID.
        body = "  " + post.text.strip().replace("\n", "\n  ")
        lines += ["", f"[P{number}] @{post.author_handle} · {_when(post.created_at)}", body]
    return "\n".join(lines) + "\n"


def export_inputs(
    queries: Sequence[AnyQuery],
    pooled: Mapping[str, Sequence[int]],
    corpus: Mapping[int, CorpusPost],
    out: Path,
    *,
    seed: int,
    max_posts: int,
    for_judge: str | None,
    set_dir: Path,
) -> ExportSummary:
    if out.exists() and any(out.iterdir()):
        raise EvaluationError(f"{out} is not empty")
    prompts = {name: load_prompt(file) for name, file in PROMPT_NAMES.items()}
    summary = ExportSummary()
    mapping: dict[str, Any] = {}
    written_slices: set[str] = set()
    for query in queries:
        wanted = new_posts(query, pooled.get(query.id, []), for_judge)
        wanted = [x_id for x_id in wanted if x_id in corpus]
        if not wanted:
            summary.queries_without_new_posts += 1
            continue
        random.Random(f"{seed}:{query.id}").shuffle(wanted)
        name = slice_of(query)
        inputs_dir = out / name / "inputs"
        if name not in written_slices:
            write_text_atomic(inputs_dir / PROMPT_FILE, prompts[name].text)
            written_slices.add(name)
        for index in range(0, len(wanted), max_posts):
            chunk = wanted[index : index + max_posts]
            input_id = query.id if index == 0 else f"{query.id}.{index // max_posts + 1}"
            write_text_atomic(
                inputs_dir / f"{input_id}.md", render_input(query, [corpus[x] for x in chunk])
            )
            item = ExportedInput(
                input_id, query.id, name, {f"P{n}": x_id for n, x_id in enumerate(chunk, start=1)}
            )
            summary.inputs.append(item)
            mapping[input_id] = {"query_id": query.id, "slice": name, "posts": item.posts}
    write_text_atomic(
        out / MAPPING_FILE,
        json.dumps(
            {
                "set_dir": str(set_dir),
                "seed": seed,
                "max_posts": max_posts,
                "for_judge": for_judge,
                "prompts": {name: prompt.version for name, prompt in prompts.items()},
                "inputs": mapping,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    return summary


@dataclass(frozen=True)
class SliceEstimate:
    calls: int
    tokens: int


@dataclass(frozen=True)
class ExportEstimate:
    slices: dict[str, SliceEstimate]

    @property
    def calls(self) -> int:
        return sum(item.calls for item in self.slices.values())

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.slices.values())


def estimate_export(out: Path) -> ExportEstimate:
    if not (out / MAPPING_FILE).exists():
        raise EvaluationError(f"{out} holds no export (mapping.json is missing)")
    slices: dict[str, SliceEstimate] = {}
    for name in PROMPT_NAMES:
        inputs_dir = out / name / "inputs"
        if not inputs_dir.is_dir():
            continue
        prompt_chars = len((inputs_dir / PROMPT_FILE).read_text(encoding="utf-8"))
        calls = tokens = 0
        for path in sorted(inputs_dir.glob("*.md")):
            if path.name == PROMPT_FILE:
                continue
            characters = prompt_chars + len(path.read_text(encoding="utf-8")) + len(REPLY_FORMAT)
            calls += 1
            tokens += characters // CHARS_PER_TOKEN + CALL_OVERHEAD_TOKENS
        slices[name] = SliceEstimate(calls, tokens)
    return ExportEstimate(slices)


@dataclass
class ImportReport:
    stored: int = 0
    unchanged: int = 0
    failures: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def read_mapping(export: Path) -> dict[str, Any]:
    path = export / MAPPING_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvaluationError(f"{export} holds no export (mapping.json is unreadable)") from None


def _check_labels(input_id: str, data: Any, expected: Mapping[str, int]) -> str | None:
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, dict):
        return f"{input_id}: the verdict has no labels object"
    given = set(labels)
    missing = sorted(set(expected) - given)
    extra = sorted(given - set(expected))
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if extra:
        problems.append(f"unknown {', '.join(extra)}")
    bad = sorted(key for key in given & set(expected) if not isinstance(labels[key], bool))
    if bad:
        problems.append(f"not a boolean {', '.join(bad)}")
    return f"{input_id}: {'; '.join(problems)}" if problems else None


def import_verdicts(
    queries: list[AnyQuery], export: Path, judge: str, *, force: bool = False
) -> tuple[list[AnyQuery], ImportReport]:
    if judge == OWNER:
        raise EvaluationError(f"{OWNER!r} is the owner's review, not a judge name")
    mapping = read_mapping(export)
    for_judge = mapping.get("for_judge")
    if for_judge is not None and for_judge != judge:
        raise EvaluationError(
            f"the export was made for {for_judge}, not {judge}; import it as {for_judge}"
        )
    prompt_versions = mapping.get("prompts") or {}
    by_id = {query.id: index for index, query in enumerate(queries)}
    report = ImportReport()
    result = list(queries)
    for input_id, item in mapping["inputs"].items():
        path = export / item["slice"] / "verdicts" / f"{input_id}.json"
        if not path.exists():
            report.missing.append(input_id)
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            report.failures.append(f"{input_id}: the verdict is not valid JSON")
            continue
        problem = _check_labels(input_id, data, item["posts"])
        if problem is not None:
            report.failures.append(problem)
            continue
        position = by_id.get(item["query_id"])
        if position is None:
            report.failures.append(f"{input_id}: query {item['query_id']} is not in the set")
            continue
        query = result[position]
        judgements = list(query.judgements)
        added: list[Judgement] = []
        conflicts = []
        unchanged = 0
        for neutral, x_id in item["posts"].items():
            label = data["labels"][neutral]
            existing = [j for j in judgements if j.x_id == x_id and j.labelled_by == judge]
            same = [j for j in existing if j.relevant == label]
            if same and len(same) == len(existing):
                unchanged += 1
            elif existing and not force:
                conflicts.append(str(x_id))
            else:
                judgements = [
                    j for j in judgements if not (j.x_id == x_id and j.labelled_by == judge)
                ]
                added.append(
                    Judgement(
                        x_id=x_id,
                        relevant=label,
                        reviewed=False,
                        labelled_by=judge,
                        prompt_version=prompt_versions.get(item["slice"]),
                    )
                )
        if conflicts:
            report.failures.append(
                f"{input_id}: {judge} already labelled {', '.join(conflicts)} differently;"
                " pass --force to replace"
            )
            continue
        result[position] = query.model_copy(update={"judgements": [*judgements, *added]})
        report.stored += len(added)
        report.unchanged += unchanged
    return result, report


@dataclass(frozen=True)
class Disagreement:
    query_id: str
    x_id: int
    first: bool
    second: bool


@dataclass(frozen=True)
class Agreement:
    compared: int
    agreed: int
    disagreements: list[Disagreement]

    @property
    def share(self) -> float | None:
        return self.agreed / self.compared if self.compared else None


def judge_label(query: AnyQuery, x_id: int, judge: str) -> bool | None:
    found = [j for j in query.judgements if j.x_id == x_id and j.labelled_by == judge]
    return found[-1].relevant if found else None


def agreement(queries: Sequence[AnyQuery], first: str, second: str) -> Agreement:
    compared = agreed = 0
    disagreements = []
    for query in queries:
        for x_id in dict.fromkeys(j.x_id for j in query.judgements):
            a, b = judge_label(query, x_id, first), judge_label(query, x_id, second)
            if a is None or b is None:
                continue
            compared += 1
            if a == b:
                agreed += 1
            else:
                disagreements.append(Disagreement(query.id, x_id, a, b))
    return Agreement(compared, agreed, disagreements)
