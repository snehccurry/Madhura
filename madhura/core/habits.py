"""STUB — owned by [H]. Pure habit logic (scheduling, streaks, due status, grouping).

Minimal working versions created by the app shell [F3] so the app runs end-to-end; the habits
agent replaces this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from madhura.core.models import Habit, HabitLog

__all__ = ["is_scheduled", "habit_streak", "last_7", "due_status", "minutes_late", "group_of"]


def is_scheduled(habit: Habit, d: date) -> bool:
    """True when *habit* is scheduled on *d* (weekday bit set in ``habit.days``)."""
    return bool(int(habit.days) & (1 << d.weekday()))


def habit_streak(habit: Habit, logs: Iterable[HabitLog], today: date) -> tuple[int, int]:
    """(current, best) streak of scheduled days done. STUB: always (0, 0)."""
    return 0, 0


def last_7(habit: Habit, logs: Iterable[HabitLog], today: date) -> list[tuple[date, str]]:
    """Status of the last 7 days (oldest first): "done" | "skipped" | "missed" | "unscheduled"."""
    by_date = {log.date: str(log.status) for log in logs if log.habit_id == habit.id}
    out: list[tuple[date, str]] = []
    for offset in range(6, -1, -1):
        d = today - timedelta(days=offset)
        if d in by_date:
            out.append((d, by_date[d]))
        elif not is_scheduled(habit, d):
            out.append((d, "unscheduled"))
        else:
            out.append((d, "missed"))
    return out


def minutes_late(habit: Habit, now: datetime) -> int:
    """Whole minutes since the habit's time today (0 when anytime or not yet due)."""
    if not habit.time:
        return 0
    try:
        hh, mm = (int(x) for x in habit.time.split(":", 1))
    except ValueError:
        return 0
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(0, int((now - due).total_seconds() // 60))


def due_status(habit: Habit, now: datetime) -> str | None:
    """"Due now" / "15 min late" / "2 hr late", or None when not due. STUB: always None."""
    return None


def group_of(habit: Habit) -> str:
    """Time-of-day group: "morning" (<12:00), "afternoon" (12–17), "evening" (≥17:00) or "anytime"."""
    if not habit.time:
        return "anytime"
    try:
        hour = int(habit.time.split(":", 1)[0])
    except ValueError:
        return "anytime"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"
