from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.alerts.config import AlertConfig
from app.alerts.schedule import (
    alert_deadlines,
    breaking_open,
    check_rehearsal,
    deadline_key,
    due_slots,
    next_alert_deadline,
    next_wake,
    polling_window,
    rehearsal_key,
)
from app.core.errors import ConfigError
from app.worker.schedule import GameweekState

DEADLINE = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
SEASON = "2026/27"
SLOTS = (120, 30)


def at(minutes_before: float) -> datetime:
    return DEADLINE - timedelta(minutes=minutes_before)


def gameweeks(*deadlines: datetime) -> list[GameweekState]:
    return [GameweekState(n, d, False, False) for n, d in enumerate(deadlines, start=5)]


def config(slots=SLOTS, rehearsal=None) -> AlertConfig:
    return AlertConfig(slots, 3, Decimal("15"), rehearsal)


def test_deadline_keys():
    assert deadline_key(SEASON, 6) == "2026/27:gw6"
    assert rehearsal_key(datetime(2026, 10, 4, 16, 0, tzinfo=UTC)) == "rehearsal:2026-10-04T16:00Z"
    real = alert_deadlines(SEASON, gameweeks(DEADLINE), None)[0]
    assert (real.key, real.rehearsal, real.season, real.gameweek) == (
        "2026/27:gw5",
        False,
        SEASON,
        5,
    )
    assert real.deadline_at == DEADLINE


def test_a_moved_real_deadline_keeps_its_key():
    before = alert_deadlines(SEASON, gameweeks(DEADLINE), None)[0]
    after = alert_deadlines(SEASON, gameweeks(DEADLINE + timedelta(hours=3)), None)[0]
    assert before.key == after.key


def test_rehearsal_is_an_extra_alert_deadline():
    rehearsal = DEADLINE - timedelta(days=2)
    deadlines = alert_deadlines(SEASON, gameweeks(DEADLINE), rehearsal)

    assert [d.rehearsal for d in deadlines] == [True, False]
    assert deadlines[0].key == "rehearsal:2026-10-02T16:00Z"
    assert (deadlines[0].season, deadlines[0].gameweek) == (None, None)
    assert next_alert_deadline(deadlines, rehearsal - timedelta(hours=1)) == deadlines[0]
    assert next_alert_deadline(deadlines, rehearsal) == deadlines[1]
    assert next_alert_deadline(deadlines, DEADLINE) is None


def test_no_season_means_only_the_rehearsal():
    rehearsal = DEADLINE
    assert [d.rehearsal for d in alert_deadlines(None, [], rehearsal)] == [True]
    assert alert_deadlines(None, [], None) == []


def test_missed_slots_due_in_order_until_deadline():
    deadline = alert_deadlines(SEASON, gameweeks(DEADLINE), None)[0]

    assert due_slots(deadline, SLOTS, set(), at(125)) == []
    assert due_slots(deadline, SLOTS, set(), at(120)) == [120]
    assert due_slots(deadline, SLOTS, set(), at(20)) == [120, 30]
    assert due_slots(deadline, SLOTS, {120, 30}, at(20)) == []
    assert due_slots(deadline, SLOTS, {120}, at(20)) == [30]
    assert due_slots(deadline, SLOTS, set(), DEADLINE) == []
    assert due_slots(deadline, SLOTS, set(), DEADLINE + timedelta(minutes=5)) == []


def test_breaking_window():
    deadline = alert_deadlines(SEASON, gameweeks(DEADLINE), None)[0]
    assert breaking_open(deadline, True, at(10)) is True
    assert breaking_open(deadline, False, at(10)) is False
    assert breaking_open(deadline, True, DEADLINE) is False


def test_next_wake():
    deadline = alert_deadlines(SEASON, gameweeks(DEADLINE), None)[0]

    assert next_wake(None, SLOTS, set(), at(500)) == 60
    assert next_wake(deadline, SLOTS, set(), at(500)) == 60
    assert next_wake(deadline, SLOTS, set(), at(120.5)) == pytest.approx(30)
    assert next_wake(deadline, SLOTS, {120}, at(100)) == 60
    assert next_wake(deadline, SLOTS, {120, 30}, at(10)) == 5
    assert next_wake(deadline, SLOTS, {120, 30}, DEADLINE) == 60


def test_polling_window():
    assert polling_window(None) == timedelta(minutes=90)
    assert polling_window(config()) == timedelta(minutes=130)
    assert polling_window(config((60, 30))) == timedelta(minutes=90)
    assert polling_window(config((90, 30))) == timedelta(minutes=100)


def test_rehearsal_past_ignored_and_overlap_rejected():
    now = DEADLINE - timedelta(days=3)
    window = timedelta(minutes=130)

    check_rehearsal(config(rehearsal=None), [DEADLINE], now)
    check_rehearsal(config(rehearsal=now - timedelta(hours=1)), [now - timedelta(hours=1)], now)
    check_rehearsal(config(rehearsal=DEADLINE - timedelta(days=2)), [DEADLINE], now)
    check_rehearsal(config(rehearsal=DEADLINE + window), [DEADLINE], now)  # adjacent
    check_rehearsal(config(rehearsal=DEADLINE - window), [DEADLINE], now)  # adjacent

    for rehearsal in (
        DEADLINE,
        DEADLINE + timedelta(minutes=129),
        DEADLINE - timedelta(minutes=129),
    ):
        with pytest.raises(ConfigError, match="ALERT_REHEARSAL_DEADLINE"):
            check_rehearsal(config(rehearsal=rehearsal), [DEADLINE], now)

    just_passed = DEADLINE - timedelta(days=7)
    with pytest.raises(ConfigError, match="ALERT_REHEARSAL_DEADLINE"):
        check_rehearsal(
            config(rehearsal=just_passed + timedelta(minutes=60)),
            [just_passed, DEADLINE],
            just_passed + timedelta(minutes=30),
        )
