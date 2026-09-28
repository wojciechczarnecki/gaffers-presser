from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKeyConstraint, Index
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class Extraction(SQLModel, table=True):
    __tablename__ = "extraction"
    __table_args__ = (
        ForeignKeyConstraint(["tweet_x_id"], ["tweet.x_id"]),
        Index(
            "ix_extraction_tweet_status_finished",
            "tweet_x_id",
            "status",
            "finished_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    tweet_x_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str  # extracted | failed
    provider: str
    model: str
    prompt_version: str
    started_at: datetime = Field(sa_column=utc_column())
    finished_at: datetime = Field(sa_column=utc_column())
    attempts: int
    error_class: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_seconds: float | None = None


class ExtractionEvent(SQLModel, table=True):
    __tablename__ = "extraction_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["player_season", "player_fpl_id"], ["player.season", "player.fpl_id"]
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    extraction_id: int = Field(foreign_key="extraction.id", index=True)
    mention: str
    team_mention: str | None = None
    player_season: str | None = None
    player_fpl_id: int | None = None
    event_type: str
    certainty: str
