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
from app.retrieval.evaluation.dataset import CorpusPost, EvaluationError, PlayerQuery
from app.retrieval.evaluation.labels import AnyQuery
from app.retrieval.evaluation.pooling import new_posts

CALL_OVERHEAD_TOKENS = 3000
CHARS_PER_TOKEN = 4
DEFAULT_MAX_POSTS = 30
PROMPT_FILE = "judge-prompt.md"
MAPPING_FILE = "mapping.json"
PROMPT_NAMES = {"player": "retrieval_relevance_player", "free": "retrieval_relevance_free"}
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
        body = post.text.strip().replace("\n", "\n  ")
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
