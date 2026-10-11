"""Work-hours logic for planning nudges (spec §1.7). Pure, no Qt."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

__all__ = ["HHMM_RE", "parse_hhmm", "in_work_hours", "work_window"]

HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def parse_hhmm(value: str | time) -> time:
    """Parse ``"HH:MM"`` (or pass a ``time`` through). Raises ValueError on bad input."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if not isinstance(value, str) or not HHMM_RE.match(value):
        raise ValueError(f"expected HH:MM, got {value!r}")
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _day_in_mask(d: date, mask: int) -> bool:
    return bool(mask & (1 << d.weekday()))


def in_work_hours(dt: datetime, days_mask: int, start: str | time, end: str | time) -> bool:
    """True when *dt* falls inside the configured work window.

    * ``start == end`` → all day (on days in the mask).
    * ``start < end``  → ``start <= t < end`` on days in the mask.
    * ``start > end``  → wraps past midnight; the day mask applies to the day the window *starts*.
    """
    start_t = parse_hhmm(start)
    end_t = parse_hhmm(end)
    t = dt.time().replace(tzinfo=None)
    today = dt.date()
    if start_t == end_t:
        return _day_in_mask(today, days_mask)
    if start_t < end_t:
        return _day_in_mask(today, days_mask) and start_t <= t < end_t
    if t >= start_t:
        return _day_in_mask(today, days_mask)
    if t < end_t:
        return _day_in_mask(today - timedelta(days=1), days_mask)
    return False


def work_window(d: date, start: str | time, end: str | time) -> tuple[datetime, datetime]:
    """Naive ``[start, end)`` datetimes of the work window starting on *d* (end may be next day)."""
    start_t = parse_hhmm(start)
    end_t = parse_hhmm(end)
    begin = datetime.combine(d, start_t)
    if start_t == end_t:
        return datetime.combine(d, time(0, 0)), datetime.combine(d + timedelta(days=1), time(0, 0))
    finish = datetime.combine(d, end_t)
    if end_t < start_t:
        finish += timedelta(days=1)
    return begin, finish
