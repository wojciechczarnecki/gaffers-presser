import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.tweets.models import Tweet, TweetPoll
from app.tweets.sources.base import FetchedPost

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollRecord:
    source: str
    started_at: datetime
    finished_at: datetime
    outcome: str  # succeeded | failed | rate_limited
    new_posts: int
    error_class: str | None = None
    retry_after_seconds: float | None = None


def _to_poll_record(row: TweetPoll) -> PollRecord:
    return PollRecord(
        source=row.source,
        started_at=row.started_at,
        finished_at=row.finished_at,
        outcome=row.outcome,
        new_posts=row.new_posts,
        error_class=row.error_class,
        retry_after_seconds=row.retry_after_seconds,
    )


def store_posts(
    session: Session, posts: list[FetchedPost], source: str, fetched_at: datetime
) -> int:
    if not posts:
        return 0
    rows = [
        {
            "x_id": post.x_id,
            "author_handle": post.author_handle,
            "text": post.text,
            "created_at": post.created_at,
            "first_fetched_at": fetched_at,
            "source": source,
            "is_repost": post.is_repost,
            "is_reply": post.is_reply,
            "reposted_author_handle": post.reposted_author_handle,
            "raw": post.raw,
        }
        for post in posts
    ]
    stmt = (
        insert(Tweet.__table__)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["x_id"])
        .returning(Tweet.__table__.c.x_id)
    )
    result = session.execute(stmt)
    return len(result.fetchall())


def last_seen_id(session: Session) -> int | None:
    return session.exec(select(func.max(Tweet.x_id))).one()


def write_poll(engine: Engine, record: PollRecord) -> None:
    try:
        with Session(engine) as session, session.begin():
            session.add(
                TweetPoll(
                    source=record.source,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                    outcome=record.outcome,
                    new_posts=record.new_posts,
                    error_class=record.error_class,
                    retry_after_seconds=record.retry_after_seconds,
                )
            )
    except SQLAlchemyError as exc:
        logger.error("tweet poll log write failed: %s", type(exc).__name__)


def latest_poll(engine: Engine, source: str) -> PollRecord | None:
    with Session(engine) as session:
        row = session.exec(
            select(TweetPoll)
            .where(TweetPoll.source == source)
            .order_by(TweetPoll.started_at.desc(), TweetPoll.id.desc())
        ).first()
    return _to_poll_record(row) if row is not None else None


def latest_success_by_source(engine: Engine) -> dict[str, PollRecord]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT ON (source) * FROM tweet_poll WHERE outcome = 'succeeded'"
                " ORDER BY source, started_at DESC, id DESC"
            )
        ).mappings()
        return {row["source"]: _to_poll_record(TweetPoll(**dict(row))) for row in rows}
