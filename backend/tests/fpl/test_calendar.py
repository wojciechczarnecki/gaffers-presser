from datetime import UTC, datetime, timedelta

from app.fpl.calendar import Calendar, GameweekState, current_season, load_calendar
from app.fpl.models import Gameweek, Season

D = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def _gameweek(season: str, fpl_id: int, deadline_at: datetime, finished: bool) -> Gameweek:
    return Gameweek(
        season=season,
        fpl_id=fpl_id,
        name=f"GW{fpl_id}",
        deadline_at=deadline_at,
        finished=finished,
        data_checked=finished,
    )


def test_empty_database_has_no_season(db_session):
    assert current_season(db_session) is None
    assert load_calendar(db_session.get_bind()) == Calendar(season=None, gameweeks=[])


def test_calendar_holds_the_latest_season_in_gameweek_order(db_session):
    db_session.add_all([Season(label="2025/26"), Season(label="2026/27")])
    db_session.flush()
    db_session.add_all(
        [
            _gameweek("2026/27", 7, D + timedelta(days=7), False),
            _gameweek("2026/27", 6, D, True),
            _gameweek("2025/26", 6, D - timedelta(days=365), True),
        ]
    )
    db_session.commit()

    assert current_season(db_session) == "2026/27"
    assert load_calendar(db_session.get_bind()) == Calendar(
        season="2026/27",
        gameweeks=[
            GameweekState(fpl_id=6, deadline_at=D, finished=True, data_checked=True),
            GameweekState(
                fpl_id=7, deadline_at=D + timedelta(days=7), finished=False, data_checked=False
            ),
        ],
    )
