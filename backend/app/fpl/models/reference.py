from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class Season(SQLModel, table=True):
    __tablename__ = "season"

    label: str = Field(primary_key=True)


class Gameweek(SQLModel, table=True):
    __tablename__ = "gameweek"

    season: str = Field(foreign_key="season.label", primary_key=True)
    fpl_id: int = Field(primary_key=True)
    name: str
    deadline_at: datetime = Field(sa_column=utc_column())
    finished: bool
    data_checked: bool


class Team(SQLModel, table=True):
    __tablename__ = "team"

    season: str = Field(foreign_key="season.label", primary_key=True)
    fpl_id: int = Field(primary_key=True)
    name: str
    short_name: str


class Player(SQLModel, table=True):
    __tablename__ = "player"
    __table_args__ = (
        ForeignKeyConstraint(["season", "team_fpl_id"], ["team.season", "team.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    fpl_id: int = Field(primary_key=True)
    web_name: str
    first_name: str
    second_name: str
    team_fpl_id: int
    position: int


class Fixture(SQLModel, table=True):
    __tablename__ = "fixture"
    __table_args__ = (
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
        ForeignKeyConstraint(["season", "team_h_fpl_id"], ["team.season", "team.fpl_id"]),
        ForeignKeyConstraint(["season", "team_a_fpl_id"], ["team.season", "team.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    fpl_id: int = Field(primary_key=True)
    gameweek_fpl_id: int | None = None
    kickoff_at: datetime | None = Field(default=None, sa_column=utc_column(nullable=True))
    team_h_fpl_id: int
    team_a_fpl_id: int
    team_h_score: int | None = None
    team_a_score: int | None = None
    finished: bool


class PlayerFlagChange(SQLModel, table=True):
    __tablename__ = "player_flag_change"
    __table_args__ = (
        ForeignKeyConstraint(["season", "player_fpl_id"], ["player.season", "player.fpl_id"]),
        Index(
            "ix_player_flag_change_season_player_observed",
            "season",
            "player_fpl_id",
            "observed_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    season: str = Field(foreign_key="season.label")
    player_fpl_id: int
    status: str
    news: str
    news_added: datetime | None = Field(default=None, sa_column=utc_column(nullable=True))
    chance_of_playing_this_round: int | None = None
    chance_of_playing_next_round: int | None = None
    observed_at: datetime = Field(sa_column=utc_column())
