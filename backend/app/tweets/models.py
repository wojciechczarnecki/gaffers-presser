from datetime import datetime

from sqlalchemy import BigInteger, Column, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class Tweet(SQLModel, table=True):
    __tablename__ = "tweet"
    __table_args__ = (Index("ix_tweet_created_at", "created_at"),)

    x_id: int = Field(sa_column=Column(BigInteger, primary_key=True, autoincrement=False))
    author_handle: str
    text: str
    created_at: datetime = Field(sa_column=utc_column())
    first_fetched_at: datetime = Field(sa_column=utc_column())
    source: str
    is_repost: bool
    is_reply: bool
    raw: dict = Field(sa_column=Column(JSONB, nullable=False))


class TweetPoll(SQLModel, table=True):
    __tablename__ = "tweet_poll"
    __table_args__ = (
        Index("ix_tweet_poll_source_outcome_started", "source", "outcome", "started_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str
    started_at: datetime = Field(sa_column=utc_column())
    finished_at: datetime = Field(sa_column=utc_column())
    outcome: str  # succeeded | failed | rate_limited
    new_posts: int
    error_class: str | None = None
    retry_after_seconds: float | None = None
