from datetime import datetime

from sqlmodel import Session, col, delete, select

from app.db.upsert import upsert
from app.fpl.client import FplClient
from app.fpl.errors import JobError
from app.fpl.models import (
    Fixture,
    Gameweek,
    Player,
    PlayerFixtureStat,
    PlayerGameweekResult,
    RawPayload,
)
from app.fpl.reference import apply_fixtures
from app.fpl.schemas import Fixture as FixtureSchema


def _resolve_gameweek(session: Session, gameweek: int) -> tuple[str, Gameweek]:
    row = session.exec(
        select(Gameweek).where(Gameweek.fpl_id == gameweek).order_by(Gameweek.deadline_at.desc())
    ).first()
    if row is None:
        raise JobError(f"gameweek {gameweek} does not exist")
    return row.season, row


def _replace_fixture_stats(
    session: Session, season: str, gameweek: int, fixtures: list[FixtureSchema]
) -> None:
    fixture_ids = {f.id for f in fixtures if f.event == gameweek}
    session.exec(
        delete(PlayerFixtureStat).where(
            PlayerFixtureStat.season == season,
            col(PlayerFixtureStat.fixture_fpl_id).in_(
                select(Fixture.fpl_id).where(
                    Fixture.season == season, Fixture.gameweek_fpl_id == gameweek
                )
            ),
        )
    )
    known_players = set(session.exec(select(Player.fpl_id).where(Player.season == season)))
    rows = {
        (f.id, entry.element, stat.identifier): {
            "season": season,
            "fixture_fpl_id": f.id,
            "player_fpl_id": entry.element,
            "identifier": stat.identifier,
            "side": side,
            "value": entry.value,
        }
        for f in fixtures
        if f.id in fixture_ids
        for stat in f.stats
        for side, entries in (("h", stat.h), ("a", stat.a))
        for entry in entries
        if entry.element in known_players
    }
    session.add_all(PlayerFixtureStat(**row) for row in rows.values())


def sync_results(session: Session, client: FplClient, gameweek: int, now: datetime) -> None:
    season, gw_row = _resolve_gameweek(session, gameweek)
    if not (gw_row.finished and gw_row.data_checked):
        raise JobError(f"gameweek {gameweek} is not finished and data-checked")

    live = client.live(gameweek)
    fixtures = client.fixtures()

    upsert(
        session,
        PlayerGameweekResult,
        [
            {
                "season": season,
                "player_fpl_id": el.id,
                "gameweek_fpl_id": gameweek,
                "starts": el.stats.starts,
                "minutes": el.stats.minutes,
                "total_points": el.stats.total_points,
                "explain": el.explain,
                "live_stats": el.stats.model_dump(),
            }
            for el in live.data.elements
        ],
        ["season", "player_fpl_id", "gameweek_fpl_id"],
    )
    apply_fixtures(session, season, fixtures.data)
    _replace_fixture_stats(session, season, gameweek, fixtures.data)

    session.add(
        RawPayload(
            season=season,
            endpoint=f"event/{gameweek}/live",
            gameweek_fpl_id=gameweek,
            fetched_at=now,
            payload=live.raw,
        )
    )
    session.add(
        RawPayload(
            season=season,
            endpoint="fixtures",
            gameweek_fpl_id=gameweek,
            fetched_at=now,
            payload=fixtures.raw,
        )
    )
