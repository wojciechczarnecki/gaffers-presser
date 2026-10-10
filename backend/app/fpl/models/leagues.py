from datetime import datetime

from sqlalchemy import ForeignKeyConstraint
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


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
    gameweek_rank: int | None = None


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
