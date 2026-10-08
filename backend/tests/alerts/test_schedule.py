from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from decimal import Decimal

import pytest

from app.alerts.config import AlertConfig, WallClockSlot
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
    resolve_slots,
    slot_moments,
)
from app.core.errors import ConfigError
from app.core.local_time import parse_local
from app.worker.schedule import GameweekState

DEADLINE = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
SEASON = "2026/27"
SLOTS = (120, 30)
DEFAULT_SLOTS = (WallClockSlot(1, clock_time(20, 0)), 60)


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
    assert polling_window(config()) == timedelta(minutes=90)
    assert polling_window(config((60, 30))) == timedelta(minutes=90)
    assert polling_window(config((200, 90))) == timedelta(minutes=100)
    assert polling_window(config((WallClockSlot(1, clock_time(20, 0)), 100))) == timedelta(
        minutes=110
    )
    assert polling_window(config(DEFAULT_SLOTS)) == timedelta(minutes=90)


def test_a_wall_clock_last_slot_keeps_the_short_window_and_gets_a_pre_slot_poll():
    only_digest = config((WallClockSlot(1, clock_time(20, 0)),))
    saturday = parse_local("2026-10-10 12:00")

    window = polling_window(only_digest)
    moments = slot_moments(only_digest.slots, saturday)

    assert window == timedelta(minutes=90)
    assert moments == [parse_local("2026-10-09 20:00")]
    assert moments[0] < saturday - window  # so the tweet loop adds one poll before it (AC9)


def test_rehearsal_overlap_by_alert_span():
    now = DEADLINE - timedelta(days=3)
    window = timedelta(minutes=120)

    check_rehearsal(config(rehearsal=None), [DEADLINE], now)
    check_rehearsal(config(rehearsal=now - timedelta(hours=1)), [now - timedelta(hours=1)], now)
    check_rehearsal(config(rehearsal=DEADLINE - timedelta(days=2)), [DEADLINE], now)
    check_rehearsal(config(rehearsal=DEADLINE + timedelta(hours=6)), [DEADLINE], now)
    check_rehearsal(config(rehearsal=DEADLINE + window), [DEADLINE], now)  # spans only touch
    check_rehearsal(config(rehearsal=DEADLINE - window), [DEADLINE], now)  # spans only touch

    for rehearsal in (
        DEADLINE,
        DEADLINE + timedelta(minutes=60),
        DEADLINE - timedelta(minutes=60),
        DEADLINE - timedelta(minutes=100),  # inside the real span (T-120) but before its T-90
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


def test_rehearsal_overlap_with_the_default_slots_needs_about_a_day():
    real = parse_local("2026-10-10 12:00")
    now = parse_local("2026-10-01 12:00")
    default = config(DEFAULT_SLOTS)

    for rehearsal in ("2026-10-10 18:00", "2026-10-10 10:00", "2026-10-09 21:00"):
        with pytest.raises(ConfigError, match="ALERT_REHEARSAL_DEADLINE"):
            check_rehearsal(
                AlertConfig(DEFAULT_SLOTS, 3, Decimal("15"), parse_local(rehearsal)), [real], now
            )
    for rehearsal in ("2026-10-13 18:00", "2026-10-07 18:00"):
        check_rehearsal(
            AlertConfig(DEFAULT_SLOTS, 3, Decimal("15"), parse_local(rehearsal)), [real], now
        )
    assert default.slots == DEFAULT_SLOTS


DIGEST = WallClockSlot(1, clock_time(20, 0))
DEFAULT = (DIGEST, 60)


@pytest.mark.parametrize(
    ("deadline", "digest", "news"),
    [
        ("2026-10-09 19:30", "2026-10-08 20:00", "2026-10-09 18:30"),
        ("2026-10-10 12:00", "2026-10-09 20:00", "2026-10-10 11:00"),
        ("2026-10-10 14:30", "2026-10-09 20:00", "2026-10-10 13:30"),
        ("2026-10-14 19:30", "2026-10-13 20:00", "2026-10-14 18:30"),
        ("2026-10-11 15:30", "2026-10-10 20:00", "2026-10-11 14:30"),
    ],
)
def test_default_slots_resolve_per_deadline_type(deadline, digest, news):
    deadline_at = parse_local(deadline)

    resolved = resolve_slots(DEFAULT, deadline_at)
    moments = slot_moments(DEFAULT, deadline_at)

    assert resolved.skipped == ()
    assert moments == [parse_local(digest), parse_local(news)]
    assert resolved.minutes[1] == 60


def test_wall_clock_slot_across_dst_change():
    autumn = resolve_slots(DEFAULT, parse_local("2026-10-25 15:30"))
    spring = resolve_slots(DEFAULT, parse_local("2027-03-28 15:30"))

    assert autumn.minutes == (1230, 60)
    assert spring.minutes == (1110, 60)


def test_wall_clock_slot_minutes_are_whole_minutes_to_deadline():
    deadline_at = parse_local("2026-10-10 12:00") + timedelta(seconds=30)

    resolved = resolve_slots(DEFAULT, deadline_at)

    assert resolved.minutes == (960, 60)
    assert deadline_at - timedelta(minutes=960) >= parse_local("2026-10-09 20:00")


def test_wall_clock_slot_uses_the_warsaw_date_of_the_deadline():
    resolved = resolve_slots(
        (WallClockSlot(1, clock_time(20, 0)),), parse_local("2026-10-10 00:30")
    )

    assert slot_moments(
        (WallClockSlot(1, clock_time(20, 0)),), parse_local("2026-10-10 00:30")
    ) == [parse_local("2026-10-09 20:00")]
    assert resolved.minutes == (270,)


def test_out_of_order_wall_clock_slot_is_skipped():
    saturday = parse_local("2026-10-10 12:00")
    resolved = resolve_slots((DIGEST, 1500), saturday)
    assert (resolved.minutes, resolved.skipped) == ((1500,), (DIGEST,))

    early = resolve_slots(
        (WallClockSlot(1, clock_time(23, 59)), 60), parse_local("2026-10-10 00:30")
    )
    assert (early.minutes, early.skipped) == ((60,), (WallClockSlot(1, clock_time(23, 59)),))

    two = WallClockSlot(2, clock_time(20, 0))
    ordered = resolve_slots((DIGEST, two, 60), saturday)
    assert (ordered.minutes, ordered.skipped) == ((2400, 60), (DIGEST,))

    at_the_next_slot = resolve_slots((DIGEST, 960), saturday)  # Fri 20:00 is exactly T-960
    assert (at_the_next_slot.minutes, at_the_next_slot.skipped) == ((960,), (DIGEST,))

    plain = resolve_slots((120, 30), saturday)
    assert (plain.minutes, plain.skipped) == ((120, 30), ())
