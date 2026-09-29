import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.tweets.models import Tweet

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals" / "retrieval"
DEFAULT_CORPUS_PATH = EVALS_DIR / "v1" / "corpus.jsonl"
DEFAULT_QUERIES_PATH = EVALS_DIR / "v1" / "queries.jsonl"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"


class CorpusPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_id: int
    author_handle: str
    text: str
    created_at: datetime
    is_repost: bool
    is_reply: bool


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


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    language: Literal["en", "pl"]
    origin: Literal["event", "post"]
    source_x_id: int | None
    event: QueryEvent | None
    split: Literal["dev", "test"]
    judgements: list[Judgement]


def _write_lines(path: Path, lines: Sequence[str]) -> None:
    # Atomic: a temporary file next to the target, then os.replace, so an interrupted
    # write never leaves a half-written set behind.
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_corpus(path: Path) -> list[CorpusPost]:
    return [
        CorpusPost.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def write_corpus(path: Path, posts: Sequence[CorpusPost]) -> None:
    _write_lines(path, [post.model_dump_json() for post in posts])


def load_queries(path: Path) -> list[Query]:
    return [
        Query.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]


def write_queries(path: Path, queries: Sequence[Query]) -> None:
    _write_lines(path, [query.model_dump_json() for query in queries])


def export_corpus(engine: Engine) -> list[CorpusPost]:
    with Session(engine) as session:
        tweets = session.exec(select(Tweet).order_by(Tweet.created_at, Tweet.x_id)).all()
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
