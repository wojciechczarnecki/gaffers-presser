from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from app.retrieval.models import PostEmbedding
from app.tweets.models import Tweet

RETRY_AFTER = timedelta(minutes=10)


@dataclass(frozen=True)
class PostToEmbed:
    x_id: int
    text: str
    first_fetched_at: datetime


@dataclass(frozen=True)
class ModelCounts:
    model: str
    embedded: int
    failed: int
    missing: int


@dataclass(frozen=True)
class LatestEmbedding:
    updated_at: datetime
    x_id: int
    model: str
    latency_seconds: float | None


@dataclass(frozen=True)
class EmbeddingStatus:
    posts: int
    models: list[ModelCounts]
    latest: LatestEmbedding | None
    total_cost_usd: float


def _to_post(row) -> PostToEmbed:
    return PostToEmbed(x_id=row.x_id, text=row.text, first_fetched_at=row.first_fetched_at)


def _candidates(model: str):
    return (
        select(Tweet.x_id, Tweet.text, Tweet.first_fetched_at)
        .outerjoin(
            PostEmbedding,
            (PostEmbedding.tweet_x_id == Tweet.x_id) & (PostEmbedding.model == model),
        )
        .order_by(Tweet.created_at, Tweet.x_id)
    )


def next_unembedded(
    session: Session, model: str, now: datetime, retry_after: timedelta = RETRY_AFTER
) -> PostToEmbed | None:
    row = session.execute(
        _candidates(model)
        .where(
            or_(
                PostEmbedding.id.is_(None),
                (PostEmbedding.status == "failed")
                & (PostEmbedding.updated_at <= now - retry_after),
            )
        )
        .limit(1)
    ).first()
    return _to_post(row) if row is not None else None


def posts_missing(session: Session, model: str) -> list[PostToEmbed]:
    rows = session.execute(
        _candidates(model).where(
            or_(PostEmbedding.id.is_(None), PostEmbedding.status != "embedded")
        )
    ).all()
    return [_to_post(row) for row in rows]


def save_embedded(
    session: Session,
    *,
    x_id: int,
    model: str,
    vector: list[float],
    input_tokens: int | None,
    cost_usd: float | None,
    latency_seconds: float | None,
    attempts: int,
    now: datetime,
) -> None:
    values = {
        "tweet_x_id": x_id,
        "model": model,
        "status": "embedded",
        "embedding": vector,
        "dimensions": len(vector),
        "input_tokens": input_tokens,
        "cost_usd": cost_usd,
        "latency_seconds": latency_seconds,
        "attempts": attempts,
        "error_class": None,
        "updated_at": now,
    }
    _upsert(session, values)


def save_failed(
    session: Session,
    *,
    x_id: int,
    model: str,
    error_class: str,
    attempts: int,
    now: datetime,
) -> None:
    values = {
        "tweet_x_id": x_id,
        "model": model,
        "status": "failed",
        "embedding": None,
        "dimensions": None,
        "input_tokens": None,
        "cost_usd": None,
        "latency_seconds": None,
        "attempts": attempts,
        "error_class": error_class,
        "updated_at": now,
    }
    _upsert(session, values)


def _upsert(session: Session, values: dict) -> None:
    table = PostEmbedding.__table__
    statement = insert(table).values(**values)
    update = {key: value for key, value in values.items() if key not in ("tweet_x_id", "model")}
    session.execute(
        statement.on_conflict_do_update(constraint="uq_post_embedding_tweet_model", set_=update)
    )


def embedding_status(engine: Engine, models: Iterable[str]) -> EmbeddingStatus:
    with Session(engine) as session:
        posts = session.exec(select(func.count()).select_from(Tweet)).one()
        stored = set(session.exec(select(PostEmbedding.model).distinct()).all())
        counts = []
        for model in sorted(stored | set(models)):
            embedded, failed = session.exec(
                select(
                    func.count().filter(PostEmbedding.status == "embedded"),
                    func.count().filter(PostEmbedding.status == "failed"),
                ).where(PostEmbedding.model == model)
            ).one()
            counts.append(
                ModelCounts(
                    model=model,
                    embedded=embedded,
                    failed=failed,
                    missing=posts - embedded - failed,
                )
            )
        latest_row = session.exec(
            select(PostEmbedding)
            .where(PostEmbedding.status == "embedded")
            .order_by(PostEmbedding.updated_at.desc(), PostEmbedding.id.desc())
            .limit(1)
        ).first()
        total = session.exec(select(func.coalesce(func.sum(PostEmbedding.cost_usd), 0.0))).one()
    latest = (
        LatestEmbedding(
            updated_at=latest_row.updated_at,
            x_id=latest_row.tweet_x_id,
            model=latest_row.model,
            latency_seconds=latest_row.latency_seconds,
        )
        if latest_row is not None
        else None
    )
    return EmbeddingStatus(posts=posts, models=counts, latest=latest, total_cost_usd=float(total))
