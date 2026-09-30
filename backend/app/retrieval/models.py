from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import BigInteger, Column, ForeignKey, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class PostEmbedding(SQLModel, table=True):
    __tablename__ = "post_embedding"
    __table_args__ = (
        UniqueConstraint("tweet_x_id", "model", name="uq_post_embedding_tweet_model"),
        Index("ix_post_embedding_model_status", "model", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    tweet_x_id: int = Field(sa_column=Column(BigInteger, ForeignKey("tweet.x_id"), nullable=False))
    model: str
    status: str  # embedded | failed
    embedding: list[float] | None = Field(default=None, sa_column=Column(VECTOR(), nullable=True))
    dimensions: int | None = None
    input_tokens: int | None = None
    cost_usd: float | None = None
    latency_seconds: float | None = None
    attempts: int
    error_class: str | None = None
    updated_at: datetime = Field(sa_column=utc_column())
