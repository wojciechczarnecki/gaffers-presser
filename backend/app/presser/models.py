from datetime import datetime

from sqlalchemy import Column, ForeignKeyConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class Presser(SQLModel, table=True):
    __tablename__ = "presser"
    __table_args__ = (
        ForeignKeyConstraint(["season", "league_fpl_id"], ["league.season", "league.fpl_id"]),
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
        Index(
            "ix_presser_league_gameweek",
            "season",
            "league_fpl_id",
            "gameweek_fpl_id",
            "created_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    season: str = Field(foreign_key="season.label")
    league_fpl_id: int
    gameweek_fpl_id: int
    idempotency_key: str = Field(index=True)
    facts: dict = Field(sa_column=Column(JSONB, nullable=False))
    text: str | None = None
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_seconds: float | None = None
    status: str  # generated | sent | failed
    error_class: str | None = None
    delivery_log_id: int | None = Field(default=None, foreign_key="delivery_log.id")
    trace_id: str | None = None
    created_at: datetime = Field(sa_column=utc_column())
