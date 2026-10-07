from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.fpl.deadlines import (
    catch_up_floor,
    deadline_at_or_before,
    latest_deadline_at_or_before,
    next_deadline_after,
    upcoming_deadlines,
)
from app.fpl.models.reference import Gameweek, Season

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


def test_catch_up_floor_is_the_previous_deadline_in_a_normal_week():
    previous, next_ = NOW - timedelta(days=3), NOW + timedelta(days=4)
    assert catch_up_floor(previous, next_, timedelta(days=7), NOW) == previous


def test_catch_up_floor_caps_a_long_break_at_the_lookback_before_the_next_deadline():
    previous, next_ = NOW - timedelta(days=10), NOW + timedelta(days=4)
    assert catch_up_floor(previous, next_, timedelta(days=7), NOW) == NOW - timedelta(days=3)


def test_catch_up_floor_without_a_next_deadline_counts_back_from_now():
    previous = NOW - timedelta(days=10)
    assert catch_up_floor(previous, None, timedelta(days=7), NOW) == NOW - timedelta(days=7)


def test_catch_up_floor_without_a_previous_deadline_uses_the_lookback():
    next_ = NOW + timedelta(days=2)
    assert catch_up_floor(None, next_, timedelta(days=7), NOW) == NOW - timedelta(days=5)
    assert catch_up_floor(None, None, timedelta(days=7), NOW) == NOW - timedelta(days=7)
