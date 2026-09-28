import logging
import tomllib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, func, select

from app.fpl.models.reference import Player, Team

logger = logging.getLogger(__name__)

DEFAULT_ALIASES_PATH = Path(__file__).parent / "aliases.toml"

# Characters NFKD does not decompose into a base letter plus combining marks.
_EXTRA_CHAR_MAP = str.maketrans(
    {
        "ø": "o",
        "æ": "ae",
        "œ": "oe",
        "ß": "ss",
        "đ": "d",
        "ł": "l",
        "ı": "i",
        "þ": "th",
    }
)
_PUNCTUATION_MAP = str.maketrans({c: " " for c in ".'’-"})

# (season, fpl_id) pairs already warned about for a missing / stale player alias, so the
# warning is logged once per process even across several `PlayerIndex` builds.
_logged_stale_aliases: set[tuple[str, int]] = set()


def normalise(value: str) -> str:
    value = value.casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.translate(_EXTRA_CHAR_MAP)
    value = value.translate(_PUNCTUATION_MAP)
    return " ".join(value.split())


@dataclass(frozen=True)
class PlayerRecord:
    season: str
    fpl_id: int
    web_name: str
    first_name: str
    second_name: str
    team_fpl_id: int


@dataclass(frozen=True)
class TeamRecord:
    season: str
    fpl_id: int
    name: str
    short_name: str


@dataclass(frozen=True)
class PlayerAlias:
    alias: str
    season: str
    fpl_id: int


@dataclass(frozen=True)
class TeamAlias:
    alias: str
    short_name: str


def load_aliases(path: Path = DEFAULT_ALIASES_PATH) -> tuple[list[PlayerAlias], list[TeamAlias]]:
    data = tomllib.loads(path.read_text())
    players = [PlayerAlias(**entry) for entry in data.get("player", [])]
    teams = [TeamAlias(**entry) for entry in data.get("team", [])]
    return players, teams


def load_players(session: Session) -> tuple[list[PlayerRecord], list[TeamRecord]]:
    season = session.exec(select(func.max(Player.season))).one_or_none()
    if season is None:
        return [], []
    players = [
        PlayerRecord(
            season=p.season,
            fpl_id=p.fpl_id,
            web_name=p.web_name,
            first_name=p.first_name,
            second_name=p.second_name,
            team_fpl_id=p.team_fpl_id,
        )
        for p in session.exec(select(Player).where(Player.season == season)).all()
    ]
    teams = [
        TeamRecord(season=t.season, fpl_id=t.fpl_id, name=t.name, short_name=t.short_name)
        for t in session.exec(select(Team).where(Team.season == season)).all()
    ]
    return players, teams


def load_snapshot(path: Path) -> tuple[list[PlayerRecord], list[TeamRecord]]:
    import json

    data = json.loads(Path(path).read_text())
    players = [PlayerRecord(**entry) for entry in data["players"]]
    teams = [TeamRecord(**entry) for entry in data["teams"]]
    return players, teams


def _player_keys(player: PlayerRecord) -> set[str]:
    keys = {
        normalise(player.web_name),
        normalise(player.first_name),
        normalise(player.second_name),
        normalise(f"{player.first_name} {player.second_name}"),
    }
    last_token = player.second_name.split()[-1] if player.second_name.split() else ""
    if last_token:
        keys.add(normalise(last_token))
        keys.add(normalise(f"{player.first_name} {last_token}"))
    keys.discard("")
    return keys


class PlayerIndex:
    def __init__(
        self,
        players: list[PlayerRecord],
        teams: list[TeamRecord],
        player_aliases: list[PlayerAlias] = (),
        team_aliases: list[TeamAlias] = (),
    ) -> None:
        self._by_key: dict[str, set[PlayerRecord]] = defaultdict(set)
        for player in players:
            for key in _player_keys(player):
                self._by_key[key].add(player)

        players_by_season_id = {(p.season, p.fpl_id): p for p in players}
        for alias in player_aliases:
            player = players_by_season_id.get((alias.season, alias.fpl_id))
            if player is None:
                stale_key = (alias.season, alias.fpl_id)
                if stale_key not in _logged_stale_aliases:
                    _logged_stale_aliases.add(stale_key)
                    logger.warning(
                        "player alias points to a player missing from the current season"
                    )
                continue
            key = normalise(alias.alias)
            if key:
                self._by_key[key].add(player)

        self._teams_by_key: dict[str, TeamRecord] = {}
        for team in teams:
            for key in (normalise(team.name), normalise(team.short_name)):
                if key:
                    self._teams_by_key.setdefault(key, team)
        teams_by_short_name = {team.short_name: team for team in teams}
        for team_alias in team_aliases:
            team = teams_by_short_name.get(team_alias.short_name)
            if team is None:
                continue
            key = normalise(team_alias.alias)
            if key:
                self._teams_by_key.setdefault(key, team)

    def resolve(self, mention: str, team: str | None) -> list[PlayerRecord]:
        candidates = self._by_key.get(normalise(mention))
        if not candidates:
            return []
        ordered = sorted(candidates, key=lambda p: p.fpl_id)
        if team:
            team_record = self._teams_by_key.get(normalise(team))
            if team_record is not None:
                narrowed = [p for p in ordered if p.team_fpl_id == team_record.fpl_id]
                if narrowed:
                    return narrowed
        return ordered
