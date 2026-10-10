"""Forgiving day streaks (spec §1.9). Pure, no Qt."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

__all__ = ["day_streak"]


def _is_work_day(d: date, mask: int) -> bool:
    return bool(mask & (1 << d.weekday()))


def day_streak(goal_dates: Iterable[date], today: date, work_days_mask: int) -> tuple[int, int]:
    """Return ``(current, best)`` streaks of days that met the daily goal.

    Rules: a met day always counts; an unmet *non-work* day is neutral (never breaks); an unmet
    *today* is neutral (the day isn't over); an unmet past work day breaks the streak.
    Dates after *today* are ignored.
    """
    met = {d for d in goal_dates if d <= today}
    if not met:
        return 0, 0
    earliest = min(met)

    current = 0
    day = today
    while day >= earliest:
        if day in met:
            current += 1
        elif day != today and _is_work_day(day, work_days_mask):
            break
        day -= timedelta(days=1)

    best = 0
    run = 0
    day = earliest
    while day <= today:
        if day in met:
            run += 1
            best = max(best, run)
        elif day != today and _is_work_day(day, work_days_mask):
            run = 0
        day += timedelta(days=1)
    return current, max(best, current)
