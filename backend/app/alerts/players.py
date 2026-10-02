from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlmodel import Session

from app.alerts.config import AlertConfig
from app.alerts.schemas import ListedPlayer, ManagerRef
from app.corroboration.rules import account_of
from app.corroboration.schemas import PlayerRef, PostRef
from app.extraction.store import current_extractions

LAST_PICK_POSITION = 15


def _claims(
    session: Session, season: str, start: datetime, as_of: datetime
) -> dict[int, list[tuple[int, str]]]:
    claims: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for row in current_extractions(session, created_from=start, created_until=as_of):
        post = PostRef(
            row.tweet_x_id,
            row.author_handle,
            row.reposted_author_handle,
            row.is_repost,
            row.created_at,
            row.text,
        )
        named = {
            event.player_fpl_id
            for event in row.events
            if event.player_season == season and event.player_fpl_id is not None
        }
        for fpl_id in named:
            claims[fpl_id].append((row.tweet_x_id, account_of(post)))
    return claims


def _league_owned(
    session: Session, season: str, league_ids: list[int]
) -> dict[int, list[ManagerRef]]:
    rows = session.execute(
        text(
            "WITH members AS ("
            " SELECT DISTINCT entry_id FROM league_membership"
            " WHERE season = :season AND league_fpl_id = ANY(:league_ids)),"
            " latest AS (SELECT max(p.gameweek_fpl_id) AS gameweek FROM manager_pick p"
            "  JOIN members m ON m.entry_id = p.entry_id WHERE p.season = :season)"
            " SELECT p.player_fpl_id, mg.manager_name, mg.team_name FROM manager_pick p"
            " JOIN members m ON m.entry_id = p.entry_id"
            " JOIN manager mg ON mg.season = p.season AND mg.entry_id = p.entry_id"
            " JOIN latest l ON l.gameweek = p.gameweek_fpl_id"
            " WHERE p.season = :season AND p.position <= :last_position"
            " ORDER BY mg.manager_name, mg.entry_id"
        ),
        {"season": season, "league_ids": league_ids, "last_position": LAST_PICK_POSITION},
    )
    owned: dict[int, list[ManagerRef]] = defaultdict(list)
    for row in rows:
        owned[row.player_fpl_id].append(ManagerRef(row.manager_name, row.team_name))
    return owned


def _widely_owned(session: Session, season: str, threshold: Decimal) -> set[int]:
    rows = session.execute(
        text(
            "SELECT fpl_id FROM player WHERE season = :season AND selected_by_percent >= :threshold"
        ),
        {"season": season, "threshold": threshold},
    )
    return {row.fpl_id for row in rows}


def _player_rows(session: Session, season: str, fpl_ids: set[int]) -> dict[int, tuple]:
    if not fpl_ids:
        return {}
    rows = session.execute(
        text(
            "SELECT p.fpl_id, p.web_name, p.selected_by_percent, t.name AS team_name"
            " FROM player p LEFT JOIN team t ON t.season = p.season AND t.fpl_id = p.team_fpl_id"
            " WHERE p.season = :season AND p.fpl_id = ANY(:ids)"
        ),
        {"season": season, "ids": sorted(fpl_ids)},
    )
    return {row.fpl_id: tuple(row) for row in rows}


def listed_players(
    session: Session,
    season: str,
    league_ids: list[int],
    start: datetime,
    as_of: datetime,
    config: AlertConfig,
) -> tuple[list[ListedPlayer], int]:
    claims = _claims(session, season, start, as_of)
    owned = _league_owned(session, season, league_ids)
    widely = _widely_owned(session, season, config.widely_owned_percent)
    trending = {
        fpl_id
        for fpl_id, posts in claims.items()
        if len({account for _, account in posts}) >= config.trending_min_accounts
    }
    listed_ids = set(owned) | widely | trending
    rows = _player_rows(session, season, listed_ids)

    reported = []
    for fpl_id in sorted(listed_ids & set(claims)):
        if fpl_id not in rows:
            continue
        _, web_name, percent, team_name = rows[fpl_id]
        accounts = {account for _, account in claims[fpl_id]}
        reported.append(
            ListedPlayer(
                player=PlayerRef(season, fpl_id, web_name, team_name),
                selected_by_percent=percent,
                managers=tuple(owned.get(fpl_id, ())),
                widely_owned=fpl_id in widely,
                trending_accounts=len(accounts) if fpl_id in trending else None,
                claim_x_ids=tuple(sorted({x_id for x_id, _ in claims[fpl_id]})),
            )
        )
    reported.sort(
        key=lambda item: (
            item.selected_by_percent is None,
            -(item.selected_by_percent or Decimal(0)),
            item.player.web_name,
        )
    )
    without_claim = len(listed_ids - set(claims))
    return reported, without_claim
