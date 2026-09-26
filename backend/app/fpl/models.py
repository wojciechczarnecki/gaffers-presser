from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Numeric
from sqlmodel import Field, SQLModel


def utc_column(*, nullable: bool = False, primary_key: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable, primary_key=primary_key)


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
    selected_by_percent: Decimal = Field(sa_column=Column(Numeric(5, 1)))
    now_cost: int
    captured_at: datetime = Field(sa_column=utc_column())


class RawPayload(SQLModel, table=True):
    __tablename__ = "raw_payload"

    id: int | None = Field(default=None, primary_key=True)
    season: str = Field(foreign_key="season.label")
    endpoint: str
    gameweek_fpl_id: int | None = None
    fetched_at: datetime = Field(sa_column=utc_column())
    payload: dict = Field(sa_column=Column(JSONB))


class League(SQLModel, table=True):
    __tablename__ = "league"

    season: str = Field(foreign_key="season.label", primary_key=True)
    fpl_id: int = Field(primary_key=True)
    name: str


class Manager(SQLModel, table=True):
    __tablename__ = "manager"

    season: str = Field(foreign_key="season.label", primary_key=True)
    entry_id: int = Field(primary_key=True)
    team_name: str
    manager_name: str


class LeagueMembership(SQLModel, table=True):
    __tablename__ = "league_membership"
    __table_args__ = (
        ForeignKeyConstraint(["season", "league_fpl_id"], ["league.season", "league.fpl_id"]),
        ForeignKeyConstraint(["season", "entry_id"], ["manager.season", "manager.entry_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    league_fpl_id: int = Field(primary_key=True)
    entry_id: int = Field(primary_key=True)


class LeagueStanding(SQLModel, table=True):
    __tablename__ = "league_standing"
    __table_args__ = (
        ForeignKeyConstraint(["season", "league_fpl_id"], ["league.season", "league.fpl_id"]),
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
        ForeignKeyConstraint(["season", "entry_id"], ["manager.season", "manager.entry_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    league_fpl_id: int = Field(primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    entry_id: int = Field(primary_key=True)
    rank: int
    event_total: int
    total: int


class ManagerGameweek(SQLModel, table=True):
    __tablename__ = "manager_gameweek"
    __table_args__ = (
        ForeignKeyConstraint(["season", "entry_id"], ["manager.season", "manager.entry_id"]),
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    entry_id: int = Field(primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    has_team: bool
    active_chip: str | None = None
    points: int | None = None
    total_points: int | None = None
    event_transfers: int | None = None
    event_transfers_cost: int | None = None
    points_on_bench: int | None = None
    bank: int | None = None
    value: int | None = None
    overall_rank: int | None = None


class ManagerPick(SQLModel, table=True):
    __tablename__ = "manager_pick"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "entry_id", "gameweek_fpl_id"],
            [
                "manager_gameweek.season",
                "manager_gameweek.entry_id",
                "manager_gameweek.gameweek_fpl_id",
            ],
        ),
        ForeignKeyConstraint(["season", "player_fpl_id"], ["player.season", "player.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    entry_id: int = Field(primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    position: int = Field(primary_key=True)
    player_fpl_id: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool


class ManagerAutoSub(SQLModel, table=True):
    __tablename__ = "manager_auto_sub"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season", "entry_id", "gameweek_fpl_id"],
            [
                "manager_gameweek.season",
                "manager_gameweek.entry_id",
                "manager_gameweek.gameweek_fpl_id",
            ],
        ),
        ForeignKeyConstraint(["season", "player_out_fpl_id"], ["player.season", "player.fpl_id"]),
        ForeignKeyConstraint(["season", "player_in_fpl_id"], ["player.season", "player.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    entry_id: int = Field(primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    player_out_fpl_id: int = Field(primary_key=True)
    player_in_fpl_id: int


class ManagerTransfer(SQLModel, table=True):
    __tablename__ = "manager_transfer"
    __table_args__ = (
        ForeignKeyConstraint(["season", "entry_id"], ["manager.season", "manager.entry_id"]),
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
        ForeignKeyConstraint(["season", "player_in_fpl_id"], ["player.season", "player.fpl_id"]),
        ForeignKeyConstraint(["season", "player_out_fpl_id"], ["player.season", "player.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    entry_id: int = Field(primary_key=True)
    made_at: datetime = Field(sa_column=utc_column(primary_key=True))
    player_in_fpl_id: int = Field(primary_key=True)
    gameweek_fpl_id: int
    player_out_fpl_id: int
    player_in_cost: int
    player_out_cost: int


class ManagerChip(SQLModel, table=True):
    __tablename__ = "manager_chip"
    __table_args__ = (
        ForeignKeyConstraint(["season", "entry_id"], ["manager.season", "manager.entry_id"]),
        ForeignKeyConstraint(["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]),
    )

    season: str = Field(foreign_key="season.label", primary_key=True)
    entry_id: int = Field(primary_key=True)
    name: str = Field(primary_key=True)
    gameweek_fpl_id: int = Field(primary_key=True)
    played_at: datetime = Field(sa_column=utc_column())


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
    explain: list = Field(sa_column=Column(JSONB))
