import logging
import tomllib
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from sqlmodel import Session, select

from app.fpl.calendar import current_season
from app.fpl.models.reference import Player, Team

logger = logging.getLogger(__name__)

DEFAULT_ALIASES_PATH = Path(__file__).parent / "aliases.toml"
DEFAULT_SHORT_FIRST_NAMES_PATH = Path(__file__).parent / "short_first_names.toml"

# Key tiers, best first: a mention resolves to the players of the best tier it matches.
NAME_TIER = 0
SURNAME_PART_TIER = 1
FIRST_NAME_TIER = 2
_TIERS = (NAME_TIER, SURNAME_PART_TIER, FIRST_NAME_TIER)

# Name particles that never identify a player on their own ("de" in "dos Santos").
_SURNAME_PARTICLES = frozenset(
    {
        "da",
        "das",
        "de",
        "del",
        "della",
        "di",
        "do",
        "dos",
        "du",
        "e",
        "el",
        "la",
        "le",
        "van",
        "von",
        "der",
        "den",
        "y",
    }
)

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


@cache
def load_short_first_names(
    path: Path = DEFAULT_SHORT_FIRST_NAMES_PATH,
) -> dict[str, tuple[str, ...]]:
    forms = tomllib.loads(path.read_text()).get("forms", {})
    return {normalise(name): tuple(normalise(f) for f in short) for name, short in forms.items()}


def load_players(session: Session) -> tuple[list[PlayerRecord], list[TeamRecord]]:
    season = current_season(session)
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


def _player_keys(
    player: PlayerRecord, short_first_names: dict[str, tuple[str, ...]]
) -> dict[str, int]:
    first_name = normalise(player.first_name)
    second_name = normalise(player.second_name)
    first_names = [first_name, *short_first_names.get(first_name, ())]
    # A hyphenated surname ("Gibbs-White") stays one token here and splits into parts below.
    surname_tokens = [
        token
        for token in (normalise(raw) for raw in player.second_name.split())
        if token not in _SURNAME_PARTICLES
    ]
    surname_parts = {part for token in surname_tokens for part in token.split()}

    keys: dict[str, int] = {}

    def add(key: str, tier: int) -> None:
        if key and tier < keys.get(key, len(_TIERS)):
            keys[key] = tier

    add(normalise(player.web_name), NAME_TIER)
    add(second_name, NAME_TIER)
    if surname_tokens:
        add(surname_tokens[-1], NAME_TIER)
    first_name_parts = {part for name in first_names for part in name.split()}
    for name in [*first_names, *first_name_parts]:
        add(f"{name} {second_name}", NAME_TIER)
        for token in surname_tokens:
            add(f"{name} {token}", NAME_TIER)
    for part in surname_parts | set(surname_tokens):
        if len(part) > 2 and part not in _SURNAME_PARTICLES:
            add(part, SURNAME_PART_TIER)
    for name in first_names:
        add(name, FIRST_NAME_TIER)
    for part in first_name_parts:
        if len(part) > 2:
            add(part, FIRST_NAME_TIER)
    return keys


class PlayerIndex:
    def __init__(
        self,
        players: list[PlayerRecord],
        teams: list[TeamRecord],
        player_aliases: list[PlayerAlias] = (),
        team_aliases: list[TeamAlias] = (),
        short_first_names: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        if short_first_names is None:
            short_first_names = load_short_first_names()
        self._by_tier: tuple[dict[str, set[PlayerRecord]], ...] = tuple(
            defaultdict(set) for _ in _TIERS
        )
        self._player_by_fpl_id: dict[int, PlayerRecord] = {p.fpl_id: p for p in players}
        for player in players:
            for key, tier in _player_keys(player, short_first_names).items():
                self._by_tier[tier][key].add(player)

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
                self._by_tier[NAME_TIER][key].add(player)

        self._team_by_fpl_id: dict[int, TeamRecord] = {team.fpl_id: team for team in teams}

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

    def player(self, fpl_id: int) -> PlayerRecord | None:
        return self._player_by_fpl_id.get(fpl_id)

    def teams(self) -> list[TeamRecord]:
        return sorted(self._team_by_fpl_id.values(), key=lambda t: t.fpl_id)

    def team_for(self, team_fpl_id: int) -> TeamRecord | None:
        return self._team_by_fpl_id.get(team_fpl_id)

    def resolve(
        self, mention: str, team: str | None, every_tier: bool = False
    ) -> list[PlayerRecord]:
        return self._resolve(mention, team, every_tier)[1]

    def resolve_post(self, mentions: Sequence[tuple[str, str | None]]) -> list[list[PlayerRecord]]:
        resolved = [self._resolve(mention, team) for mention, team in mentions]
        clubs = {
            candidates[0].team_fpl_id
            for tier, candidates in resolved
            if tier == NAME_TIER and len(candidates) == 1
        }
        narrowed = []
        for _, candidates in resolved:
            at_clubs = [p for p in candidates if p.team_fpl_id in clubs]
            narrowed.append(at_clubs if len(candidates) > 1 and at_clubs else candidates)
        return narrowed

    def _resolve(
        self, mention: str, team: str | None, every_tier: bool = False
    ) -> tuple[int | None, list[PlayerRecord]]:
        key = normalise(mention)
        tiers = [(tier, by_key[key]) for tier, by_key in enumerate(self._by_tier) if key in by_key]
        if every_tier and tiers:
            tiers = [(tiers[0][0], set().union(*(candidates for _, candidates in tiers)))]
        team_record = self._teams_by_key.get(normalise(team)) if team else None
        if team_record is not None:
            for tier, candidates in tiers:
                at_team = [p for p in candidates if p.team_fpl_id == team_record.fpl_id]
                if at_team:
                    return tier, sorted(at_team, key=lambda p: p.fpl_id)
        if not tiers:
            return None, []
        tier, candidates = tiers[0]
        return tier, sorted(candidates, key=lambda p: p.fpl_id)
