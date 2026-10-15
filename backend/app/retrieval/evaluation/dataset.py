from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Tag, TypeAdapter
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.core.errors import AppError
from app.core.eval_results import write_json_atomic, write_lines_atomic
from app.tweets.models import Tweet

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals" / "retrieval"
V1_DIR = EVALS_DIR / "v1"
DEFAULT_SET_DIR = EVALS_DIR / "v2"
DEFAULT_CORPUS_PATH = V1_DIR / "corpus.jsonl"
DEFAULT_QUERIES_PATH = V1_DIR / "queries.jsonl"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"
CORPUS_FILE = "corpus.jsonl"
QUERIES_FILE = "queries.jsonl"
MANIFEST_FILE = "set.json"


class SetError(AppError):
    pass


class CorpusPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_id: int
    author_handle: str
    text: str
    created_at: datetime
    is_repost: bool
    is_reply: bool
    quoted_x_id: int | None = None
    is_source: bool | None = None


class Judgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_id: int
    relevant: bool
    reviewed: bool
    labelled_by: str


class QueryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player: str
    event_type: str


class FreeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["free"] = "free"
    id: str
    text: str
    language: Literal["en", "pl"]
    origin: Literal["event", "post"]
    source_x_id: int | None
    event: QueryEvent | None
    split: Literal["dev", "test"]
    judgements: list[Judgement]


Query = FreeQuery


class PlayerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: str
    fpl_id: int
    web_name: str
    first_name: str
    second_name: str
    team: str
    aliases: list[str]


class ClaimedPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_id: int
    created_at: datetime
    stale: bool


WindowRule = Literal["alerts", "after-last-match"]


class PlayerQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["player"] = "player"
    id: str
    split: Literal["dev", "test"]
    player: PlayerInfo
    gameweek: int
    deadline_at: datetime
    window_start: datetime
    window_end: datetime
    window_rule: WindowRule
    previous_last_match_end: datetime | None
    claimed: list[ClaimedPost]
    judgements: list[Judgement]


def _kind(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("kind", "free")
    return getattr(value, "kind", "free")


AnyQuery = Annotated[
    Annotated[FreeQuery, Tag("free")] | Annotated[PlayerQuery, Tag("player")],
    Discriminator(_kind),
]
_QUERY_ADAPTER: TypeAdapter[FreeQuery | PlayerQuery] = TypeAdapter(AnyQuery)


class SetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    list_handles: list[str] | None = None
    corpus_since: datetime | None = None
    corpus_until: datetime | None = None
    exported_at: datetime | None = None


@dataclass(frozen=True)
class EvalSet:
    version: str
    manifest: SetManifest | None
    corpus: list[CorpusPost]
    queries: list[FreeQuery | PlayerQuery]
    directory: Path


def load_corpus(path: Path) -> list[CorpusPost]:
    return [
        CorpusPost.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def write_corpus(path: Path, posts: Sequence[CorpusPost]) -> None:
    write_lines_atomic(path, [post.model_dump_json(exclude_none=True) for post in posts])


def load_queries(path: Path) -> list[FreeQuery | PlayerQuery]:
    return [
        _QUERY_ADAPTER.validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def write_queries(path: Path, queries: Sequence[FreeQuery | PlayerQuery]) -> None:
    write_lines_atomic(path, [query.model_dump_json() for query in queries])


def load_manifest(directory: Path) -> SetManifest | None:
    path = Path(directory) / MANIFEST_FILE
    if not path.exists():
        return None
    return SetManifest.model_validate_json(path.read_text())


def write_manifest(directory: Path, manifest: SetManifest) -> None:
    write_json_atomic(Path(directory) / MANIFEST_FILE, manifest.model_dump(mode="json"))


def load_set(directory: Path) -> EvalSet:
    directory = Path(directory)
    manifest = load_manifest(directory)
    try:
        corpus = load_corpus(directory / CORPUS_FILE)
        queries = (
            load_queries(directory / QUERIES_FILE) if (directory / QUERIES_FILE).exists() else []
        )
    except (OSError, ValueError) as exc:
        raise SetError(f"cannot read the set in {directory}: {type(exc).__name__}") from None
    version = manifest.version if manifest else directory.name
    return EvalSet(version, manifest, corpus, queries, directory)


def carry_v1(
    v1_queries: Sequence[FreeQuery | PlayerQuery],
    v2_queries: Sequence[FreeQuery | PlayerQuery],
    corpus_ids: set[int],
) -> list[FreeQuery | PlayerQuery]:
    free = [query for query in v1_queries if isinstance(query, FreeQuery)]
    if any(isinstance(query, FreeQuery) for query in v2_queries):
        raise SetError("the set already holds free queries; pass --force to replace them")
    wanted = {j.x_id for query in free for j in query.judgements}
    missing = wanted - corpus_ids
    if missing:
        raise SetError(
            f"{len(missing)} posts of the v1 queries are missing from the v2 corpus;"
            " export the corpus without --since or from v1's first post"
        )
    return [*v2_queries, *free]


# The project's own test account (the tweet-source latency test posts) is noise for retrieval.
EXCLUDED_AUTHORS = frozenset({"gafferpresser"})


def export_corpus(engine: Engine) -> list[CorpusPost]:
    with Session(engine) as session:
        tweets = session.exec(
            select(Tweet)
            .where(func.lower(Tweet.author_handle).not_in(EXCLUDED_AUTHORS))
            .order_by(Tweet.created_at, Tweet.x_id)
        ).all()
        return [
            CorpusPost(
                x_id=tweet.x_id,
                author_handle=tweet.author_handle,
                text=tweet.text,
                created_at=tweet.created_at,
                is_repost=tweet.is_repost,
                is_reply=tweet.is_reply,
            )
            for tweet in tweets
        ]
