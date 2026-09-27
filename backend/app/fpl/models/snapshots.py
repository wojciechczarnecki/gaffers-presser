from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Column, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Numeric
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class DeadlineSnapshotPlayer(SQLModel, table=True):
    __tablename__ = "deadline_snapshot_player"
    __table_args__ = (
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
        ForeignKeyConstraint(["season", "player_fpl_id"], ["player.season", "player.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    player_fpl_id: int = Field(primary_key=True)
    status: str
    news: str
    chance_of_playing_this_round: int | None = None
    chance_of_playing_next_round: int | None = None
    selected_by_percent: Decimal = Field(sa_column=Column(Numeric(5, 1), nullable=False))
    now_cost: int
    captured_at: datetime = Field(sa_column=utc_column())


class PlayerGameweekResult(SQLModel, table=True):
    __tablename__ = "player_gameweek_result"
    __table_args__ = (
        ForeignKeyConstraint(["season", "player_fpl_id"], ["player.season", "player.fpl_id"]),
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    player_fpl_id: int = Field(primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    starts: int
    minutes: int
    total_points: int
    explain: list = Field(sa_column=Column(JSONB, nullable=False))


class RawPayload(SQLModel, table=True):
    __tablename__ = "raw_payload"

    id: int | None = Field(default=None, primary_key=True)
    season: str = Field(foreign_key="season.label")
    endpoint: str
    gameweek_fpl_id: int | None = None
    fetched_at: datetime = Field(sa_column=utc_column())
    payload: Any = Field(sa_column=Column(JSONB, nullable=False))
