from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from madhura.core import days
from madhura.core.clock import Clock, FakeClock, SystemClock

try:
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 - tzdata may be missing on some boxes
    NY = None

needs_tz = pytest.mark.skipif(NY is None, reason="zoneinfo America/New_York unavailable")


# ---------------------------------------------------------------- clock


def test_system_clock_is_a_clock() -> None:
    clock = SystemClock()
    assert isinstance(clock, Clock)
    now = clock.now()
    assert now.tzinfo is None
    assert clock.time() > 1_700_000_000


def test_fake_clock_naive_advance_and_set() -> None:
    clock = FakeClock(datetime(2026, 10, 14, 10, 0))
    assert isinstance(clock, Clock)
    t0 = clock.time()
    assert clock.now() == datetime(2026, 10, 14, 10, 0)
    clock.advance(90.5)
    assert clock.time() == pytest.approx(t0 + 90.5)
    assert clock.now() == datetime(2026, 10, 14, 10, 1, 30, 500000)
    clock.set(datetime(2026, 10, 14, 9, 0))
    assert clock.now() == datetime(2026, 10, 14, 9, 0)
    assert clock.time() == pytest.approx(t0 - 3600)
    clock.advance(-60)
    assert clock.now() == datetime(2026, 10, 14, 8, 59)
    clock.set_time(t0)
    assert clock.now() == datetime(2026, 10, 14, 10, 0)
    assert "FakeClock" in repr(clock)


def test_fake_clock_now_is_naive() -> None:
    clock = FakeClock(datetime(2026, 1, 1))
    assert clock.now().tzinfo is None


@needs_tz
def test_fake_clock_dst_spring_forward() -> None:
    # 2026-03-08 02:00 EST → 03:00 EDT
    clock = FakeClock(datetime(2026, 3, 8, 1, 30), tz=NY)
    t0 = clock.time()
    clock.advance(3600)
    assert clock.now() == datetime(2026, 3, 8, 3, 30)
    assert clock.time() - t0 == 3600
    assert clock.tz is NY


@needs_tz
def test_fake_clock_dst_fall_back() -> None:
    # 2026-11-01 02:00 EDT → 01:00 EST: 01:30 happens twice
    clock = FakeClock(datetime(2026, 11, 1, 0, 30), tz=NY)
    clock.advance(3600)
    assert clock.now() == datetime(2026, 11, 1, 1, 30)
    clock.advance(3600)
    assert clock.now() == datetime(2026, 11, 1, 1, 30)
    clock.advance(3600)
    assert clock.now() == datetime(2026, 11, 1, 2, 30)


# ---------------------------------------------------------------- days


@pytest.mark.parametrize(
    ("dt", "hour", "expected"),
    [
        (datetime(2026, 10, 14, 10, 0), 4, date(2026, 10, 14)),
        (datetime(2026, 10, 14, 3, 59), 4, date(2026, 10, 13)),
        (datetime(2026, 10, 14, 4, 0), 4, date(2026, 10, 14)),
        (datetime(2026, 10, 14, 0, 0), 0, date(2026, 10, 14)),
        (datetime(2026, 10, 14, 5, 59), 6, date(2026, 10, 13)),
        (datetime(2026, 1, 1, 1, 0), 4, date(2025, 12, 31)),
    ],
)
def test_logical_date(dt: datetime, hour: int, expected: date) -> None:
    assert days.logical_date(dt, hour) == expected


def test_day_bounds() -> None:
    start, end = days.day_bounds(date(2026, 10, 14), 4)
    assert start == datetime(2026, 10, 14, 4, 0)
    assert end == datetime(2026, 10, 15, 4, 0)
    start0, end0 = days.day_bounds(date(2026, 10, 14), 0)
    assert (start0, end0) == (datetime(2026, 10, 14), datetime(2026, 10, 15))
    # a time in [start, end) maps back to the same logical date
    for minutes in (0, 600, 1439):
        assert days.logical_date(start + timedelta(minutes=minutes), 4) == date(2026, 10, 14)


def test_week_start_is_monday() -> None:
    assert days.week_start(date(2026, 10, 14)) == date(2026, 10, 12)  # Wed → Mon
    assert days.week_start(date(2026, 10, 12)) == date(2026, 10, 12)
    assert days.week_start(date(2026, 10, 18)) == date(2026, 10, 12)  # Sun → Mon


def test_weekday_bits_and_work_days() -> None:
    assert days.weekday_bit(date(2026, 10, 12)) == 1  # Monday
    assert days.weekday_bit(date(2026, 10, 18)) == 64  # Sunday
    assert days.is_work_day(date(2026, 10, 16), days.WEEKDAYS)  # Fri
    assert not days.is_work_day(date(2026, 10, 17), days.WEEKDAYS)  # Sat
    assert days.is_work_day(date(2026, 10, 17), days.WEEKENDS)


@pytest.mark.parametrize(
    ("d", "mask", "expected"),
    [
        (date(2026, 10, 14), days.WEEKDAYS, date(2026, 10, 15)),  # Wed → Thu
        (date(2026, 10, 16), days.WEEKDAYS, date(2026, 10, 19)),  # Fri → Mon
        (date(2026, 10, 17), days.WEEKDAYS, date(2026, 10, 19)),  # Sat → Mon
        (date(2026, 10, 16), days.ALL_DAYS, date(2026, 10, 17)),
        (date(2026, 10, 16), 0, date(2026, 10, 17)),
        (date(2026, 10, 14), 1, date(2026, 10, 19)),  # only Mondays
    ],
)
def test_next_work_day(d: date, mask: int, expected: date) -> None:
    assert days.next_work_day(d, mask) == expected


def test_previous_work_day_and_range() -> None:
    assert days.previous_work_day(date(2026, 10, 19), days.WEEKDAYS) == date(2026, 10, 16)
    assert days.previous_work_day(date(2026, 10, 19), 0) == date(2026, 10, 18)
    assert list(days.date_range(date(2026, 10, 30), date(2026, 11, 2))) == [
        date(2026, 10, 30),
        date(2026, 10, 31),
        date(2026, 11, 1),
        date(2026, 11, 2),
    ]
    assert list(days.date_range(date(2026, 10, 2), date(2026, 10, 1))) == []
    assert time(4) == days.day_bounds(date(2026, 10, 1), 4)[0].time()
