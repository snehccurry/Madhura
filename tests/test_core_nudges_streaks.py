from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from madhura.core.nudges import in_work_hours, parse_hhmm, work_window
from madhura.core.streaks import day_streak

WEEKDAYS = 31
WED = date(2026, 10, 14)


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm))


# ---------------------------------------------------------------- work hours


def test_parse_hhmm() -> None:
    assert parse_hhmm("09:30") == time(9, 30)
    assert parse_hhmm(time(9, 30, 15)) == time(9, 30)
    for bad in ("9:30", "24:00", "12:60", "", None, 930):
        with pytest.raises(ValueError):
            parse_hhmm(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (at(WED, 8, 59), False),
        (at(WED, 9, 0), True),
        (at(WED, 17, 59), True),
        (at(WED, 18, 0), False),
        (at(date(2026, 10, 17), 10), False),  # Saturday
        (at(date(2026, 10, 16), 10), True),  # Friday
    ],
)
def test_in_work_hours_normal(dt: datetime, expected: bool) -> None:
    assert in_work_hours(dt, WEEKDAYS, "09:00", "18:00") is expected


def test_in_work_hours_all_day_when_start_equals_end() -> None:
    assert in_work_hours(at(WED, 0, 0), WEEKDAYS, "09:00", "09:00")
    assert in_work_hours(at(WED, 23, 59), WEEKDAYS, "00:00", "00:00")
    assert not in_work_hours(at(date(2026, 10, 18), 12), WEEKDAYS, "09:00", "09:00")  # Sunday


def test_in_work_hours_wraps_past_midnight_with_start_day_mask() -> None:
    fri = date(2026, 10, 16)
    sat = date(2026, 10, 17)
    mon = date(2026, 10, 19)
    # 22:00–02:00 on weekdays
    assert in_work_hours(at(fri, 23), WEEKDAYS, "22:00", "02:00")
    assert in_work_hours(at(sat, 1, 30), WEEKDAYS, "22:00", "02:00")  # started Friday
    assert not in_work_hours(at(sat, 23), WEEKDAYS, "22:00", "02:00")  # Saturday start
    assert not in_work_hours(at(mon, 1), WEEKDAYS, "22:00", "02:00")  # started Sunday
    assert in_work_hours(at(mon, 22), WEEKDAYS, "22:00", "02:00")
    assert not in_work_hours(at(fri, 12), WEEKDAYS, "22:00", "02:00")
    assert not in_work_hours(at(sat, 2, 0), WEEKDAYS, "22:00", "02:00")


def test_in_work_hours_accepts_time_objects() -> None:
    assert in_work_hours(at(WED, 10), WEEKDAYS, time(9), time(18))


def test_work_window() -> None:
    assert work_window(WED, "09:00", "18:00") == (at(WED, 9), at(WED, 18))
    assert work_window(WED, "22:00", "02:00") == (at(WED, 22), at(WED + timedelta(days=1), 2))
    assert work_window(WED, "09:00", "09:00") == (at(WED, 0), at(WED + timedelta(days=1), 0))


# ---------------------------------------------------------------- streaks


def d(offset: int) -> date:
    return WED + timedelta(days=offset)


def test_streak_empty() -> None:
    assert day_streak([], WED, WEEKDAYS) == (0, 0)


def test_streak_counts_consecutive_including_today() -> None:
    assert day_streak([d(0), d(-1), d(-2)], WED, WEEKDAYS) == (3, 3)


def test_unmet_today_never_breaks() -> None:
    assert day_streak([d(-1), d(-2)], WED, WEEKDAYS) == (2, 2)


def test_unmet_work_day_breaks() -> None:
    # Wed met, Tue missed, Mon met
    assert day_streak([d(0), d(-2)], WED, WEEKDAYS) == (1, 1)


def test_weekend_neutral_and_counted_if_met() -> None:
    mon = date(2026, 10, 19)
    fri = date(2026, 10, 16)
    sat = date(2026, 10, 17)
    # Fri + Mon met, weekend not met → weekend neutral
    assert day_streak([fri, mon], mon, WEEKDAYS) == (2, 2)
    # Fri + Sat + Mon met → Sat counts
    assert day_streak([fri, sat, mon], mon, WEEKDAYS) == (3, 3)


def test_best_streak_tracks_history() -> None:
    goal = [d(-10), d(-9), d(-8), d(-7), d(-6), d(-1), d(0)]
    current, best = day_streak(goal, WED, 127)
    assert current == 2
    assert best == 5


def test_future_dates_ignored_and_mask_zero() -> None:
    assert day_streak([d(1), d(2)], WED, WEEKDAYS) == (0, 0)
    # no work days: every unmet day is neutral
    assert day_streak([d(-5), d(-1)], WED, 0) == (2, 2)
