# Importing this package registers every collector table on SQLModel.metadata.

from app.fpl.models.leagues import (
    League,
    LeagueMembership,
    LeagueStanding,
    Manager,
    ManagerAutoSub,
    ManagerChip,
    ManagerGameweek,
    ManagerPick,
    ManagerTransfer,
)
from app.fpl.models.reference import (
    Fixture,
    Gameweek,
    Player,
    PlayerFlagChange,
    Season,
    Team,
)
from app.fpl.models.snapshots import (
    DeadlineSnapshotPlayer,
    PlayerGameweekResult,
    RawPayload,
)

__all__ = [
    "Season",
    "Gameweek",
    "Team",
    "Player",
    "Fixture",
    "PlayerFlagChange",
    "League",
    "Manager",
    "LeagueMembership",
    "LeagueStanding",
    "ManagerGameweek",
    "ManagerPick",
    "ManagerAutoSub",
    "ManagerTransfer",
    "ManagerChip",
    "DeadlineSnapshotPlayer",
    "PlayerGameweekResult",
    "RawPayload",
]
