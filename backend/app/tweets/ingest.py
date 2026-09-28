import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import Session

from app.tweets.sources.base import SourceRateLimitedError, TweetSource
from app.tweets.sources.paging import collect_new
from app.tweets.store import PollRecord, last_seen_id, store_posts, write_poll

logger = logging.getLogger(__name__)


def poll_once(
    engine: Engine, source: TweetSource, list_id: int, now_fn: Callable[[], datetime]
) -> PollRecord:
    started_at = now_fn()
    outcome = "succeeded"
    new_posts = 0
    error_class: str | None = None
    retry_after_seconds: float | None = None
    try:
        with Session(engine) as session:
            since_id = last_seen_id(session)
        posts = collect_new(source, list_id, since_id)
        fetched_at = now_fn()
        with Session(engine) as session, session.begin():
            new_posts = store_posts(session, posts, source.name, fetched_at)
    except SourceRateLimitedError as exc:
        outcome = "rate_limited"
        error_class = type(exc).__name__
        retry_after_seconds = exc.retry_after
    except Exception as exc:
        outcome = "failed"
        error_class = type(exc).__name__

    finished_at = now_fn()
    record = PollRecord(
        source=source.name,
        started_at=started_at,
        finished_at=finished_at,
        outcome=outcome,
        new_posts=new_posts,
        error_class=error_class,
        retry_after_seconds=retry_after_seconds,
    )
    write_poll(engine, record)

    duration = (finished_at - started_at).total_seconds()
    if error_class is None:
        logger.info(
            "tweet poll: source=%s outcome=%s new_posts=%s duration=%ss",
            source.name,
            outcome,
            new_posts,
            duration,
        )
    else:
        logger.info(
            "tweet poll: source=%s outcome=%s new_posts=%s duration=%ss error=%s",
            source.name,
            outcome,
            new_posts,
            duration,
            error_class,
        )
    return record
