"""Logical-day arithmetic (spec §1.1).

A timestamp belongs to logical date ``(dt - day_start_hour h).date()``, so with the default
``day_start_hour=4`` a session started at 01:30 counts toward the previous day.
Weekday bitmasks use Mon=bit0 … Sun=bit6 (127 = every day, 31 = Mon–Fri).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta

__all__ = [
    "ALL_DAYS",
    "WEEKDAYS",
    "WEEKENDS",
    "logical_date",
    "day_bounds",
    "week_start",
    "weekday_bit",
    "is_work_day",
    "next_work_day",
    "previous_work_day",
    "date_range",
]

ALL_DAYS = 0b1111111
WEEKDAYS = 0b0011111
WEEKENDS = 0b1100000


def logical_date(dt: datetime, day_start_hour: int) -> date:
    """Logical date of *dt*: ``(dt - day_start_hour hours).date()``."""
    return (dt - timedelta(hours=day_start_hour)).date()


def day_bounds(d: date, day_start_hour: int) -> tuple[datetime, datetime]:
    """Naive local ``[start, end)`` of logical date *d* (start = d at day_start_hour:00)."""
    start = datetime.combine(d, time(hour=day_start_hour))
    return start, start + timedelta(days=1)


def week_start(d: date) -> date:
    """Monday of the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())


def weekday_bit(d: date) -> int:
    """Bit for *d*'s weekday in a Mon=bit0 … Sun=bit6 mask."""
    return 1 << d.weekday()


def is_work_day(d: date, mask: int) -> bool:
    """True when *d*'s weekday is set in *mask*."""
    return bool(mask & weekday_bit(d))


def next_work_day(d: date, mask: int) -> date:
    """First date after *d* whose weekday is in *mask* (``d + 1`` when *mask* is empty)."""
    if not mask & ALL_DAYS:
        return d + timedelta(days=1)
    nxt = d + timedelta(days=1)
    while not is_work_day(nxt, mask):
        nxt += timedelta(days=1)
    return nxt


def previous_work_day(d: date, mask: int) -> date:
    """Last date before *d* whose weekday is in *mask* (``d - 1`` when *mask* is empty)."""
    if not mask & ALL_DAYS:
        return d - timedelta(days=1)
    prev = d - timedelta(days=1)
    while not is_work_day(prev, mask):
        prev -= timedelta(days=1)
    return prev


def date_range(start: date, end: date) -> Iterator[date]:
    """Dates from *start* to *end* inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
