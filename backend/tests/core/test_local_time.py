from datetime import UTC, datetime, timedelta

import pytest

from app.core.local_time import WARSAW, format_local, format_local_day, parse_local


def test_a_naive_summer_time_is_read_as_warsaw():
    assert parse_local("2026-09-29T18:00") == datetime(2026, 9, 29, 16, 0, tzinfo=UTC)


def test_a_naive_winter_time_is_read_as_warsaw():
    assert parse_local("2026-12-15T18:00") == datetime(2026, 12, 15, 17, 0, tzinfo=UTC)


def test_a_date_alone_means_local_midnight():
    assert parse_local("2026-09-29") == datetime(2026, 9, 28, 22, 0, tzinfo=UTC)


def test_the_result_is_timezone_aware_utc():
    moment = parse_local("2026-09-29T18:00")
    assert moment.utcoffset() == timedelta(0)


def test_an_explicit_offset_is_respected():
    assert parse_local("2026-09-29T18:00+00:00") == datetime(2026, 9, 29, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", ""])
def test_an_invalid_string_raises_value_error(value):
    with pytest.raises(ValueError):
        parse_local(value)


def test_format_local_renders_warsaw_time():
    assert format_local(datetime(2026, 9, 29, 16, 0, tzinfo=UTC)) == "2026-09-29 18:00"
    assert format_local(datetime(2026, 12, 15, 17, 0, tzinfo=UTC)) == "2026-12-15 18:00"


def test_the_round_trip_keeps_the_minute():
    assert format_local(parse_local("2026-09-29T18:30")) == "2026-09-29 18:30"
    assert format_local(parse_local("2026-12-15T09:05")) == "2026-12-15 09:05"
    assert WARSAW.key == "Europe/Warsaw"


def test_format_local_day():
    assert format_local_day(datetime(2026, 10, 9, 18, 0, tzinfo=UTC)) == "Fri 2026-10-09 20:00"
    assert format_local_day(datetime(2026, 10, 11, 22, 30, tzinfo=UTC)) == "Mon 2026-10-12 00:30"
