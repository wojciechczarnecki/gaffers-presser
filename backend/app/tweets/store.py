import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, and_, func, literal_column, or_, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.tweets.models import Tweet, TweetPoll
from app.tweets.sources.base import FetchedPost, merge_fetched

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
    merged: dict[int, FetchedPost] = {}
    for post in posts:
        known = merged.get(post.x_id)
        merged[post.x_id] = post if known is None else merge_fetched(known, post)
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
            "embedded": post.embedded,
            "quoted_x_id": post.quoted_x_id,
            "raw": post.raw,
        }
        for post in merged.values()
    ]
    table = Tweet.__table__
    stmt = insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["x_id"],
        set_={
            "embedded": table.c.embedded & stmt.excluded.embedded,
            "quoted_x_id": func.coalesce(table.c.quoted_x_id, stmt.excluded.quoted_x_id),
        },
        where=or_(
            and_(table.c.embedded, ~stmt.excluded.embedded),
            and_(table.c.quoted_x_id.is_(None), stmt.excluded.quoted_x_id.is_not(None)),
        ),
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    result = session.execute(stmt)
    return sum(1 for (inserted,) in result.fetchall() if inserted)


def last_seen_id(session: Session) -> int | None:
    return session.exec(select(func.max(Tweet.x_id)).where(Tweet.embedded.is_(False))).one()


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
