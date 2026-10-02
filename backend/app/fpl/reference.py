from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session

from app.db.upsert import upsert
from app.fpl.client import FplClient
from app.fpl.models import Fixture as FixtureRow
from app.fpl.models import Gameweek, Player, PlayerFlagChange, Season, Team
from app.fpl.schemas import Bootstrap, Element
from app.fpl.schemas import Fixture as FixtureSchema


def season_label(payload: Bootstrap) -> str:
    year = payload.events[0].deadline_time.year
    return f"{year}/{(year + 1) % 100:02d}"


def apply_bootstrap(session: Session, payload: Bootstrap, now: datetime) -> str:
    season = season_label(payload)
    upsert(session, Season, [{"label": season}], ["label"])
    upsert(
        session,
        Gameweek,
        [
            {
                "season": season,
                "fpl_id": e.id,
                "name": e.name,
                "deadline_at": e.deadline_time,
                "finished": e.finished,
                "data_checked": e.data_checked,
            }
            for e in payload.events
        ],
        ["season", "fpl_id"],
    )
    upsert(
        session,
        Team,
        [
            {"season": season, "fpl_id": t.id, "name": t.name, "short_name": t.short_name}
            for t in payload.teams
        ],
        ["season", "fpl_id"],
    )
    upsert(
        session,
        Player,
        [
            {
                "season": season,
                "fpl_id": el.id,
                "web_name": el.web_name,
                "first_name": el.first_name,
                "second_name": el.second_name,
                "team_fpl_id": el.team,
                "position": el.element_type,
                "selected_by_percent": el.selected_by_percent,
            }
            for el in payload.elements
        ],
        ["season", "fpl_id"],
    )
    _apply_flag_changes(session, season, payload.elements, now)
    return season


def _latest_flags(session: Session, season: str) -> dict[int, object]:
    rows = session.execute(
        text(
            "SELECT DISTINCT ON (player_fpl_id) player_fpl_id, status, news,"
            " chance_of_playing_this_round, chance_of_playing_next_round"
            " FROM player_flag_change WHERE season = :season"
            " ORDER BY player_fpl_id, observed_at DESC, id DESC"
        ),
        {"season": season},
    )
    return {row.player_fpl_id: row for row in rows}


def _flag_changed(prev: object, el: Element) -> bool:
    if prev is None:
        return True
    return (
        prev.status != el.status
        or prev.news != el.news
        or prev.chance_of_playing_this_round != el.chance_of_playing_this_round
        or prev.chance_of_playing_next_round != el.chance_of_playing_next_round
    )


def _apply_flag_changes(
    session: Session, season: str, elements: list[Element], now: datetime
) -> None:
    latest = _latest_flags(session, season)
    new_rows = [
        {
            "season": season,
            "player_fpl_id": el.id,
            "status": el.status,
            "news": el.news,
            "news_added": el.news_added,
            "chance_of_playing_this_round": el.chance_of_playing_this_round,
            "chance_of_playing_next_round": el.chance_of_playing_next_round,
            "observed_at": now,
        }
        for el in elements
        if _flag_changed(latest.get(el.id), el)
    ]
    if new_rows:
        session.execute(PlayerFlagChange.__table__.insert(), new_rows)


def apply_fixtures(session: Session, season: str, fixtures: list[FixtureSchema]) -> None:
    upsert(
        session,
        FixtureRow,
        [
            {
                "season": season,
                "fpl_id": f.id,
                "gameweek_fpl_id": f.event,
                "kickoff_at": f.kickoff_time,
                "team_h_fpl_id": f.team_h,
                "team_a_fpl_id": f.team_a,
                "team_h_score": f.team_h_score,
                "team_a_score": f.team_a_score,
                "finished": f.finished,
            }
            for f in fixtures
        ],
        ["season", "fpl_id"],
    )


def sync_reference(session: Session, client: FplClient, now: datetime) -> str:
    bootstrap = client.bootstrap()
    season = apply_bootstrap(session, bootstrap.data, now)
    fixtures = client.fixtures()
    apply_fixtures(session, season, fixtures.data)
    return season
