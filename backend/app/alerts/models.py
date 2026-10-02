from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class Alert(SQLModel, table=True):
    __tablename__ = "alert"
    __table_args__ = (
        UniqueConstraint("key", name="uq_alert_key"),
        Index("ix_alert_deadline_key_recorded_at", "deadline_key", "recorded_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    key: str
    deadline_key: str
    deadline_at: datetime = Field(sa_column=utc_column())
    kind: str  # digest | news | breaking
    slot_minutes: int | None = None
    trigger_x_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, ForeignKey("tweet.x_id"), nullable=True)
    )
    as_of: datetime = Field(sa_column=utc_column())
    status: str  # sent | failed | skipped
    delivery_log_id: int | None = Field(default=None, foreign_key="delivery_log.id")
    recorded_at: datetime = Field(sa_column=utc_column())


class AlertPost(SQLModel, table=True):
    __tablename__ = "alert_post"
    __table_args__ = (
        ForeignKeyConstraint(
            ["player_season", "player_fpl_id"], ["player.season", "player.fpl_id"]
        ),
        Index("ix_alert_post_tweet_x_id", "tweet_x_id"),
    )

    alert_id: int = Field(foreign_key="alert.id", primary_key=True)
    player_season: str = Field(primary_key=True)
    player_fpl_id: int = Field(primary_key=True)
    tweet_x_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("tweet.x_id"), primary_key=True, autoincrement=False
        )
    )
    freshness: str  # new | context
