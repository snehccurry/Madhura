from __future__ import annotations

from datetime import date, datetime

import pytest

from madhura.core import timefmt as tf


@pytest.mark.parametrize(
    ("secs", "expected"),
    [
        (0, "00:00"),
        (-5, "00:00"),
        (1, "00:01"),
        (59, "00:59"),
        (59.2, "01:00"),
        (60, "01:00"),
        (1380, "23:00"),
        (3599, "59:59"),
        (3599.5, "1:00:00"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (8 * 3600, "8:00:00"),
    ],
)
def test_fmt_clock(secs: float, expected: str) -> None:
    assert tf.fmt_clock(secs) == expected


@pytest.mark.parametrize(
    ("secs", "expected"),
    [(0, "0m"), (-3, "0m"), (1, "1m"), (60, "1m"), (61, "2m"), (1380, "23m"), (1350, "23m"), (5400, "90m")],
)
def test_fmt_minutes_short(secs: float, expected: str) -> None:
    assert tf.fmt_minutes_short(secs) == expected


@pytest.mark.parametrize(
    ("secs", "expected"),
    [
        (0, "0m"),
        (59, "0m"),
        (60, "1m"),
        (2700, "45m"),
        (3600, "1h"),
        (4800, "1h 20m"),
        (7200, "2h"),
        (8100, "2h 15m"),
        (8159.9, "2h 15m"),
        (-10, "0m"),
    ],
)
def test_fmt_duration(secs: float, expected: str) -> None:
    assert tf.fmt_duration(secs) == expected


def test_fmt_time() -> None:
    assert tf.fmt_time(datetime(2026, 10, 14, 14, 35), True) == "14:35"
    assert tf.fmt_time(datetime(2026, 10, 14, 14, 35), False) == "2:35 PM"
    assert tf.fmt_time(datetime(2026, 10, 14, 0, 5), False) == "12:05 AM"
    assert tf.fmt_time(datetime(2026, 10, 14, 12, 0), False) == "12:00 PM"
    assert tf.fmt_time(datetime(2026, 10, 14, 9, 7), True) == "09:07"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "Good evening"),
        (4, "Good evening"),
        (5, "Good morning"),
        (11, "Good morning"),
        (12, "Good afternoon"),
        (16, "Good afternoon"),
        (17, "Good evening"),
        (23, "Good evening"),
    ],
)
def test_greeting(hour: int, expected: str) -> None:
    assert tf.greeting(datetime(2026, 10, 14, hour, 30)) == expected


def test_dates() -> None:
    assert tf.fmt_date_long(date(2026, 10, 10)) == "Saturday, 10 October"
    assert tf.fmt_date_long(date(2026, 10, 9)) == "Friday, 9 October"
    assert tf.fmt_date_short(date(2026, 10, 14)) == "Wed 14 Oct"
    assert tf.fmt_weekday_short(date(2026, 10, 18)) == "Sun"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(15, "15m"), (25, "25m"), (45, "45m"), (60, "1h"), (90, "90m"), (120, "2h"), (35, "35m")],
)
def test_fmt_preset(minutes: int, expected: str) -> None:
    assert tf.fmt_preset(minutes) == expected


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (60, "Plan your next hour"),
        (25, "Plan your next 25 minutes"),
        (45, "Plan your next 45 minutes"),
        (15, "Plan your next 15 minutes"),
        (90, "Plan your next 1.5 hours"),
        (120, "Plan your next 2 hours"),
        (150, "Plan your next 2.5 hours"),
        (240, "Plan your next 4 hours"),
        (75, "Plan your next 75 minutes"),
        (1, "Plan your next minute"),
        (5, "Plan your next 5 minutes"),
    ],
)
def test_plan_heading(minutes: int, expected: str) -> None:
    assert tf.plan_heading(minutes) == expected
