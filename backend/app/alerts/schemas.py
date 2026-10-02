from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.corroboration.schemas import Corroboration, PlayerRef

AlertKind = Literal["digest", "news", "breaking"]


@dataclass(frozen=True)
class ManagerRef:
    manager_name: str
    team_name: str


@dataclass(frozen=True)
class ListedPlayer:
    player: PlayerRef
    selected_by_percent: Decimal | None
    managers: tuple[ManagerRef, ...]
    widely_owned: bool
    trending_accounts: int | None
    claim_x_ids: tuple[int, ...]


@dataclass(frozen=True)
class PlayerReport:
    listed: ListedPlayer
    corroboration: Corroboration
    new_x_ids: frozenset[int]
    search_failed: bool


@dataclass(frozen=True)
class AlertDeadline:
    key: str
    deadline_at: datetime
    rehearsal: bool
    season: str | None
    gameweek: int | None
