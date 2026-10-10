"""Human-facing time formatting (locale-independent English)."""

from __future__ import annotations

import math
from datetime import date, datetime

__all__ = [
    "fmt_clock",
    "fmt_minutes_short",
    "fmt_duration",
    "fmt_time",
    "greeting",
    "fmt_date_long",
    "fmt_date_short",
    "fmt_weekday_short",
    "fmt_preset",
    "plan_heading",
    "WEEKDAYS_LONG",
    "WEEKDAYS_SHORT",
    "MONTHS_LONG",
    "MONTHS_SHORT",
]

WEEKDAYS_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS_LONG = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTHS_SHORT = tuple(m[:3] for m in MONTHS_LONG)

_EPS = 1e-6


def _ceil_seconds(secs: float) -> int:
    return max(0, int(math.ceil(float(secs) - _EPS)))


def fmt_clock(secs: float) -> str:
    """Countdown display: ``"MM:SS"`` below one hour, ``"H:MM:SS"`` from one hour.

    Fractional seconds round up (countdown convention: 59.2 s left → ``"01:00"``); negative → ``"00:00"``.
    """
    total = _ceil_seconds(secs)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def fmt_minutes_short(secs: float) -> str:
    """Remaining time in whole minutes, rounded up: 1380 s → ``"23m"``, 61 s → ``"2m"``, 0 → ``"0m"``."""
    return f"{_ceil_seconds(_ceil_seconds(secs) / 60)}m"


def fmt_duration(secs: float) -> str:
    """Accumulated duration, floored to minutes: ``"2h 15m"``, ``"2h"``, ``"45m"``, ``"0m"``."""
    minutes = max(0, int(float(secs) + _EPS) // 60)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def fmt_time(dt: datetime, use_24h: bool) -> str:
    """Clock time: ``"14:35"`` (24 h) or ``"2:35 PM"`` (12 h)."""
    if use_24h:
        return f"{dt.hour:02d}:{dt.minute:02d}"
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {suffix}"


def greeting(dt: datetime) -> str:
    """``"Good morning"`` (05–11), ``"Good afternoon"`` (12–16), else ``"Good evening"``."""
    if 5 <= dt.hour < 12:
        return "Good morning"
    if 12 <= dt.hour < 17:
        return "Good afternoon"
    return "Good evening"


def fmt_date_long(d: date) -> str:
    """``"Friday, 10 October"``."""
    return f"{WEEKDAYS_LONG[d.weekday()]}, {d.day} {MONTHS_LONG[d.month - 1]}"


def fmt_date_short(d: date) -> str:
    """``"Tue 14 Oct"``."""
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS_SHORT[d.month - 1]}"


def fmt_weekday_short(d: date) -> str:
    """``"Tue"``."""
    return WEEKDAYS_SHORT[d.weekday()]


def fmt_preset(minutes: int) -> str:
    """Duration chip label: whole hours as ``"1h"``/``"2h"``, otherwise ``"25m"``/``"90m"``."""
    minutes = int(minutes)
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _fmt_hours(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def plan_heading(minutes: int) -> str:
    """Plan card title following the selected duration.

    60 → "Plan your next hour", 25 → "Plan your next 25 minutes", 90 → "Plan your next 1.5 hours",
    120 → "Plan your next 2 hours", 1 → "Plan your next minute".
    """
    minutes = int(minutes)
    if minutes == 60:
        return "Plan your next hour"
    if minutes == 1:
        return "Plan your next minute"
    if minutes > 60 and minutes % 30 == 0:
        return f"Plan your next {_fmt_hours(minutes / 60)} hours"
    return f"Plan your next {minutes} minutes"
