from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from app.fpl.models.reference import Fixture
from app.retrieval.evaluation.dataset import EvaluationError
from app.retrieval.evaluation.players import gameweek_window
from tests.corroboration.helpers import SEASON, seed_reference

D5 = datetime(2026, 9, 26, 17, 30, tzinfo=UTC)
D6 = datetime(2026, 10, 3, 17, 30, tzinfo=UTC)


def add_fixture(db, fpl_id, gameweek, kickoff):
    with Session(db) as session:
        session.add(
            Fixture(
                season=SEASON,
                fpl_id=fpl_id,
                gameweek_fpl_id=gameweek,
                kickoff_at=kickoff,
                team_h_fpl_id=1,
                team_a_fpl_id=2,
                finished=True,
            )
        )
        session.commit()


def window(db, rule, gameweek=6):
    with Session(db) as session:
        return gameweek_window(session, SEASON, gameweek, rule)


def test_alerts_window_starts_at_the_previous_deadline(db):
    seed_reference(db, {5: D5, 6: D6})
    found = window(db, "alerts")
    assert (found.window_start, found.window_end, found.deadline_at) == (D5, D6, D6)
    assert found.rule == "alerts"
    assert found.previous_last_match_end is None


def test_a_break_longer_than_the_lookback_starts_at_the_deadline_minus_the_lookback(db):
    far = D6 - timedelta(days=14)
    seed_reference(db, {5: far, 6: D6})
    assert window(db, "alerts").window_start == D6 - timedelta(days=7)


def test_the_first_gameweek_has_no_previous_deadline(db):
    seed_reference(db, {6: D6})
    assert window(db, "alerts").window_start == D6 - timedelta(days=7)


def test_after_last_match_starts_two_hours_after_the_latest_kickoff(db):
    seed_reference(db, {5: D5, 6: D6})
    add_fixture(db, 1, 5, datetime(2026, 9, 27, 14, 0, tzinfo=UTC))
    add_fixture(db, 2, 5, datetime(2026, 9, 29, 19, 0, tzinfo=UTC))
    add_fixture(db, 3, 5, None)
    add_fixture(db, 4, 6, datetime(2026, 10, 4, 14, 0, tzinfo=UTC))
    after = window(db, "after-last-match")
    assert after.window_start == datetime(2026, 9, 29, 21, 0, tzinfo=UTC)
    assert after.previous_last_match_end == after.window_start
    alerts = window(db, "alerts")
    assert alerts.previous_last_match_end == after.window_start


def test_after_last_match_without_a_fixture_names_the_previous_gameweek(db):
    seed_reference(db, {5: D5, 6: D6})
    with pytest.raises(EvaluationError, match="gameweek 5"):
        window(db, "after-last-match")


def test_an_unknown_gameweek_is_an_error(db):
    seed_reference(db, {6: D6})
    with pytest.raises(EvaluationError, match="gameweek 9"):
        window(db, "alerts", gameweek=9)
