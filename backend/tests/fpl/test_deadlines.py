from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.fpl.deadlines import (
    deadline_at_or_before,
    latest_deadline_at_or_before,
    next_deadline_after,
    previous_gameweek_end,
    upcoming_deadlines,
    window_floor,
)
from app.fpl.models.reference import Fixture, Gameweek, Season, Team

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Gw:
    id: int
    deadline_at: datetime


def test_next_deadline_after_is_strict_and_none_when_nothing_follows():
    deadlines = [NOW - timedelta(days=1), NOW, NOW + timedelta(days=3), NOW + timedelta(days=9)]
    assert next_deadline_after(deadlines, NOW) == NOW + timedelta(days=3)
    assert next_deadline_after(deadlines, NOW + timedelta(days=9)) is None
    assert next_deadline_after([], NOW) is None


def test_next_deadline_after_with_a_key_returns_the_item():
    items = [Gw(2, NOW + timedelta(days=9)), Gw(1, NOW + timedelta(days=3))]
    assert next_deadline_after(items, NOW, key=lambda g: g.deadline_at) == Gw(
        1, NOW + timedelta(days=3)
    )


def test_latest_deadline_at_or_before_is_inclusive_and_none_before_the_first():
    deadlines = [NOW - timedelta(days=9), NOW - timedelta(days=2), NOW + timedelta(days=5)]
    assert latest_deadline_at_or_before(deadlines, NOW) == NOW - timedelta(days=2)
    assert latest_deadline_at_or_before(deadlines, NOW - timedelta(days=2)) == NOW - timedelta(
        days=2
    )
    assert latest_deadline_at_or_before(deadlines, NOW - timedelta(days=10)) is None


def test_latest_deadline_at_or_before_with_a_key_returns_the_item():
    items = [Gw(1, NOW - timedelta(days=9)), Gw(2, NOW - timedelta(days=2))]
    assert latest_deadline_at_or_before(items, NOW, key=lambda g: g.deadline_at) == items[1]


def _seed(db) -> None:
    with Session(db) as session, session.begin():
        session.add(Season(label="2026/27"))
        session.flush()
        for fpl_id, days in ((5, -5), (6, 2), (7, 9)):
            session.add(
                Gameweek(
                    season="2026/27",
                    fpl_id=fpl_id,
                    name=f"Gameweek {fpl_id}",
                    deadline_at=NOW + timedelta(days=days),
                    finished=days < 0,
                    data_checked=days < 0,
                )
            )


def test_upcoming_deadlines_keeps_the_last_day_and_later(db):
    _seed(db)
    with Session(db) as session:
        assert upcoming_deadlines(session, NOW) == [
            NOW + timedelta(days=2),
            NOW + timedelta(days=9),
        ]


def test_deadline_at_or_before_from_the_database(db):
    _seed(db)
    with Session(db) as session:
        assert deadline_at_or_before(session, NOW + timedelta(days=2)) == NOW + timedelta(days=2)
        assert deadline_at_or_before(session, NOW) == NOW - timedelta(days=5)
        assert deadline_at_or_before(session, NOW - timedelta(days=6)) is None


def test_window_floor_is_the_previous_deadline_in_a_normal_week():
    previous, next_ = NOW - timedelta(days=3), NOW + timedelta(days=4)
    assert window_floor(previous, next_, timedelta(days=7), NOW) == previous


def test_window_floor_caps_a_long_break_at_the_lookback_before_the_next_deadline():
    previous, next_ = NOW - timedelta(days=10), NOW + timedelta(days=4)
    assert window_floor(previous, next_, timedelta(days=7), NOW) == NOW - timedelta(days=3)


def test_window_floor_without_a_next_deadline_counts_back_from_now():
    previous = NOW - timedelta(days=10)
    assert window_floor(previous, None, timedelta(days=7), NOW) == NOW - timedelta(days=7)


def test_window_floor_without_a_previous_deadline_uses_the_lookback():
    next_ = NOW + timedelta(days=2)
    assert window_floor(None, next_, timedelta(days=7), NOW) == NOW - timedelta(days=5)
    assert window_floor(None, None, timedelta(days=7), NOW) == NOW - timedelta(days=7)


D5 = datetime(2026, 9, 18, 17, 30, tzinfo=UTC)
D6 = D5 + timedelta(days=7)
LOOKBACK = timedelta(days=7)
SAT = D5 + timedelta(days=1, hours=-3.5)  # Sat 14:00
MON = SAT + timedelta(days=2, hours=5)  # Mon 19:00


def _calendar(db, gameweeks=(5, 6), fixtures=(), season="2026/27"):
    deadlines = {5: D5, 6: D6}
    with Session(db) as session, session.begin():
        if session.get(Season, season) is None:
            session.add(Season(label=season))
            session.flush()
            session.add(Team(season=season, fpl_id=1, name="A", short_name="A"))
            session.add(Team(season=season, fpl_id=2, name="B", short_name="B"))
            session.flush()
        for fpl_id in gameweeks:
            session.add(
                Gameweek(
                    season=season,
                    fpl_id=fpl_id,
                    name=f"Gameweek {fpl_id}",
                    deadline_at=deadlines[fpl_id],
                    finished=True,
                    data_checked=True,
                )
            )
        session.flush()
        for fpl_id, gameweek, kickoff in fixtures:
            session.add(
                Fixture(
                    season=season,
                    fpl_id=fpl_id,
                    gameweek_fpl_id=gameweek,
                    kickoff_at=kickoff,
                    team_h_fpl_id=1,
                    team_a_fpl_id=2,
                    finished=True,
                )
            )


def _floor(db, as_of, next_=D6):
    with Session(db) as session:
        end = previous_gameweek_end(session, as_of)
        assert end is None or end <= as_of
        return window_floor(end, next_, LOOKBACK, as_of)


def test_a_normal_week_starts_at_the_last_match_plus_two_hours(db):
    assert SAT < MON < D6
    _calendar(db, fixtures=[(1, 5, SAT), (2, 5, MON)])
    assert _floor(db, D6 - timedelta(hours=1)) == MON + timedelta(hours=2) > D5


def test_a_blank_gameweek_counts_the_last_remaining_match(db):
    _calendar(db, fixtures=[(1, 5, SAT), (2, 6, MON)])
    assert _floor(db, D6 - timedelta(hours=1)) == SAT + timedelta(hours=2)


def test_an_international_break_is_cut_by_the_lookback(db):
    old = D6 - timedelta(days=14)
    _calendar(db, fixtures=[(1, 5, old)])
    assert _floor(db, D6 - timedelta(hours=1)) == D6 - LOOKBACK


def test_before_the_first_gameweek_the_helper_is_none_and_the_lookback_applies(db):
    _calendar(db, gameweeks=(6,))
    as_of = D6 - timedelta(days=2)
    with Session(db) as session:
        assert previous_gameweek_end(session, as_of) is None
    assert _floor(db, as_of) == D6 - LOOKBACK


def test_without_previous_fixtures_or_kickoffs_the_previous_deadline_is_used(db):
    _calendar(db, fixtures=[(1, 5, None)])
    assert _floor(db, D6 - timedelta(hours=1)) == D5


def test_without_any_fixture_the_previous_deadline_is_used(db):
    _calendar(db)
    assert _floor(db, D6 - timedelta(hours=1)) == D5


def test_a_fixture_of_another_season_does_not_count(db):
    _calendar(db, fixtures=[(1, 5, SAT)])
    _calendar(db, gameweeks=(5,), season="2025/26", fixtures=[(1, 5, MON)])
    with Session(db) as session:
        end = previous_gameweek_end(session, D6 - timedelta(hours=1))
    assert end == SAT + timedelta(hours=2)


def test_a_match_not_yet_ended_does_not_count(db):
    _calendar(db, fixtures=[(1, 5, SAT), (2, 5, MON)])
    as_of = MON + timedelta(hours=1)
    assert _floor(db, as_of) == SAT + timedelta(hours=2)


def test_no_match_ended_gives_the_previous_deadline(db):
    _calendar(db, fixtures=[(1, 5, SAT)])
    as_of = SAT + timedelta(hours=1)
    assert _floor(db, as_of) == D5


def test_the_gameweek_can_be_chosen_apart_from_the_moment(db):
    _calendar(db, fixtures=[(1, 5, SAT), (2, 5, MON)])
    with Session(db) as session:
        assert previous_gameweek_end(session, D6, gameweek_at=D5) == MON + timedelta(hours=2)
        assert previous_gameweek_end(session, D6, gameweek_at=D5 - timedelta(days=1)) is None
