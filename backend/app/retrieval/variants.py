import re
from dataclasses import dataclass
from typing import Literal

from sqlmodel import Session

from app.extraction.linking import PlayerAlias, load_aliases
from app.fpl.models.reference import Player

Variant = Literal["web_name", "full_name", "names"]
VARIANTS: tuple[Variant, ...] = ("web_name", "full_name", "names")

_INITIAL = re.compile(r"^[A-Za-z]\.\s*")


@dataclass(frozen=True)
class PlayerNames:
    web_name: str
    first_name: str = ""
    second_name: str = ""
    aliases: tuple[str, ...] = ()


def has_initial(web_name: str) -> bool:
    return _INITIAL.match(web_name) is not None


def variant_text(names: PlayerNames, variant: Variant) -> str:
    if variant == "web_name":
        return names.web_name
    if variant == "full_name":
        first, second = names.first_name.strip(), names.second_name.strip()
        return f"{first} {second}" if first and second else names.web_name
    tokens = names.second_name.split()
    parts = [_INITIAL.sub("", names.web_name), tokens[-1] if tokens else "", *names.aliases]
    seen: set[str] = set()
    kept = []
    for part in parts:
        part = part.strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            kept.append(part)
    return " ".join(kept) or names.web_name


def load_player_names(
    session: Session, season: str, fpl_id: int, aliases: list[PlayerAlias] | None = None
) -> PlayerNames | None:
    player = session.get(Player, (season, fpl_id))
    if player is None:
        return None
    if aliases is None:
        aliases = load_aliases()[0]
    return PlayerNames(
        web_name=player.web_name,
        first_name=player.first_name,
        second_name=player.second_name,
        aliases=tuple(a.alias for a in aliases if (a.season, a.fpl_id) == (season, fpl_id)),
    )
