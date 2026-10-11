"""Pure habit logic (spec §1.8): scheduling, streaks, the last-7-days strip, due status, grouping.

No Qt and no wall clock: callers pass ``today`` (use ``ctx.today()``, the *logical* date),
``now`` (``ctx.clock.now()``) and ``day_start_hour`` (``ctx.day_start_hour()``; a habit created
before the day start belongs to the previous logical day). Weekday masks use Mon = bit 0 … Sun = bit 6.

Day states used across the habits UI:

``"done"``         a 'done' log exists for the day
``"skipped"``      a 'skipped' log exists (neutral: never breaks or extends a streak)
``"missed"``       scheduled, on/after the habit was created, before today, no log
``"pending"``      today, scheduled, no log yet
``"unscheduled"``  not a scheduled day (or before the habit existed) and no log
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from madhura.core.days import logical_date
from madhura.core.models import COUNTED_OUTCOMES, Habit, HabitLog

__all__ = [
    "GROUPS",
    "GROUP_LABELS",
    "STATES",
    "LATENESS_CAP_MINUTES",
    "SNOOZE_MINUTES",
    "MAX_SNOOZES_PER_DAY",
    "habit_time",
    "is_scheduled",
    "created_date",
    "logs_by_date",
    "day_state",
    "last_7",
    "habit_streak",
    "previous_scheduled_day",
    "missed_yesterday",
    "due_at",
    "minutes_late",
    "lateness_label",
    "due_status",
    "group_of",
    "sort_key",
    "focused_minutes",
    "auto_satisfied",
    "today_counts",
    "days_label",
]

#: Time-of-day groups in display order.
GROUPS: tuple[str, ...] = ("morning", "afternoon", "evening", "anytime")
GROUP_LABELS: dict[str, str] = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening", "anytime": "Anytime"}
#: Every state :func:`day_state` / :func:`last_7` can return.
STATES: tuple[str, ...] = ("done", "skipped", "missed", "unscheduled", "pending")

#: A reminder first noticed more than this many minutes late is not shown (the card says "Pending").
LATENESS_CAP_MINUTES = 120
#: "Snooze 15m" (and closing the reminder panel) snoozes for this long.
SNOOZE_MINUTES = 15
#: After this many snoozes of one habit in a day, stop reminding it that day.
MAX_SNOOZES_PER_DAY = 3

_DUE_NOW_MINUTES = 2
_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_ALL_DAYS = 0b1111111


# --------------------------------------------------------------------------- basics


def habit_time(habit: Habit) -> time | None:
    """The habit's ``"HH:MM"`` as a :class:`time`, or None for anytime (or an invalid value)."""
    raw = (habit.time or "").strip()
    if not raw:
        return None
    try:
        hh, mm = raw.split(":", 1)
        return time(int(hh), int(mm[:2]))
    except (ValueError, TypeError):
        return None


def is_scheduled(habit: Habit, d: date) -> bool:
    """True when *d*'s weekday is set in ``habit.days``."""
    return bool(int(habit.days or 0) & _ALL_DAYS & (1 << d.weekday()))


def created_date(habit: Habit, *, day_start_hour: int = 0) -> date | None:
    """Logical date the habit was created (days before it never count as missed).

    A habit created at 01:30 with a 04:00 day start belongs to the previous logical day.
    """
    created = habit.created_at
    if created is None:
        return None
    if isinstance(created, datetime):
        return logical_date(created, day_start_hour)
    return created


def logs_by_date(habit: Habit, logs: Iterable[HabitLog]) -> dict[date, str]:
    """``{date: "done"|"skipped"}`` for *habit* (logs of other habits are ignored)."""
    out: dict[date, str] = {}
    for log in logs:
        if log.habit_id == habit.id:
            out[log.date] = str(log.status)
    return out


def _before_creation(habit: Habit, d: date, day_start_hour: int = 0) -> bool:
    created = created_date(habit, day_start_hour=day_start_hour)
    return created is not None and d < created


def day_state(habit: Habit, by_date: Mapping[date, str], d: date, today: date, *, day_start_hour: int = 0) -> str:
    """State of *habit* on *d* (see the module docstring). Logs always win over the schedule."""
    status = by_date.get(d)
    if status in ("done", "skipped"):
        return status
    if d > today or not is_scheduled(habit, d) or _before_creation(habit, d, day_start_hour):
        return "unscheduled"
    if d == today:
        return "pending"
    return "missed"


def last_7(habit: Habit, logs: Iterable[HabitLog], today: date, *, day_start_hour: int = 0) -> list[tuple[date, str]]:
    """``[(date, state)]`` for the 7 days ending *today*, oldest first."""
    by_date = logs_by_date(habit, logs)
    days = (today - timedelta(days=i) for i in range(6, -1, -1))
    return [(d, day_state(habit, by_date, d, today, day_start_hour=day_start_hour)) for d in days]


def habit_streak(habit: Habit, logs: Iterable[HabitLog], today: date, *, day_start_hour: int = 0) -> tuple[int, int]:
    """``(current, best)`` run of consecutive *scheduled* days marked done.

    'skipped' days are neutral, unscheduled days are ignored (even when done), days before the
    habit was created are ignored, and today without a log never breaks the run — so the current
    streak ends today (if done) or yesterday.
    """
    by_date = logs_by_date(habit, logs)
    start = created_date(habit, day_start_hour=day_start_hour) or today
    if by_date:
        start = min(start, min(by_date))
    start = min(start, today)
    run = best = 0
    d = start
    one = timedelta(days=1)
    while d <= today:
        if is_scheduled(habit, d):
            status = by_date.get(d)
            if status == "done":
                run += 1
                best = max(best, run)
            elif status == "skipped" or d == today or _before_creation(habit, d, day_start_hour):
                pass
            else:
                run = 0
        d += one
    return run, best


def previous_scheduled_day(habit: Habit, d: date, limit: int = 7) -> date | None:
    """The last scheduled day strictly before *d* (looking back at most *limit* days)."""
    for i in range(1, limit + 1):
        prev = d - timedelta(days=i)
        if is_scheduled(habit, prev):
            return prev
    return None


def missed_yesterday(habit: Habit, logs: Iterable[HabitLog], today: date, *, day_start_hour: int = 0) -> bool:
    """"Never miss twice": scheduled today, nothing logged today, and the previous scheduled day
    (yesterday for a daily habit) was missed — not done, not skipped, not before creation."""
    if not is_scheduled(habit, today):
        return False
    by_date = logs_by_date(habit, logs)
    if today in by_date:
        return False
    prev = previous_scheduled_day(habit, today)
    if prev is None or _before_creation(habit, prev, day_start_hour):
        return False
    return prev not in by_date


# --------------------------------------------------------------------------- reminders


def due_at(habit: Habit, d: date, day_start_hour: int = 0) -> datetime | None:
    """When *habit* is due on logical date *d* (None for anytime habits).

    Times before ``day_start_hour`` belong to the end of the logical day (e.g. 01:00 with a 04:00
    day start is due on the next calendar date).
    """
    t = habit_time(habit)
    if t is None:
        return None
    due = datetime.combine(d, t)
    if t.hour < day_start_hour:
        due += timedelta(days=1)
    return due


def minutes_late(habit: Habit, now: datetime, *, day_start_hour: int = 0) -> int:
    """Whole minutes since the habit's time on the logical day of *now* (0 if anytime/not yet)."""
    due = due_at(habit, logical_date(now, day_start_hour), day_start_hour)
    if due is None or now <= due:
        return 0
    return int((now - due).total_seconds() // 60)


def lateness_label(minutes: int) -> str:
    """``"Due now"`` / ``"15 min late"`` / ``"2 hr late"``."""
    minutes = max(0, int(minutes))
    if minutes < _DUE_NOW_MINUTES:
        return "Due now"
    if minutes < 60:
        return f"{minutes} min late"
    return f"{minutes // 60} hr late"


def due_status(
    habit: Habit,
    now: datetime,
    log: Any = None,
    snooze_until: datetime | None = None,
    *,
    day_start_hour: int = 0,
    auto_done: bool = False,
) -> str | None:
    """Reminder label (:func:`lateness_label`) when *habit* is due at *now*, else None.

    Due means: not archived, reminder on, has a time, scheduled on today's logical date, no log
    today (*log* = today's log/status or None), the time has been reached, the habit existed
    before that time, not snoozed (*snooze_until*) and not already satisfied by focus time
    (*auto_done*). The lateness cap is the caller's job (it depends on when it was first seen).
    """
    if habit.archived or not habit.reminder or auto_done or log is not None:
        return None
    today = logical_date(now, day_start_hour)
    if not is_scheduled(habit, today):
        return None
    due = due_at(habit, today, day_start_hour)
    if due is None or now < due:
        return None
    created = habit.created_at
    if isinstance(created, datetime) and created > due:
        return None
    if snooze_until is not None and now < snooze_until:
        return None
    return lateness_label(minutes_late(habit, now, day_start_hour=day_start_hour))


# --------------------------------------------------------------------------- grouping


def group_of(habit: Habit, *, day_start_hour: int = 0) -> str:
    """"morning" (< 12:00), "afternoon" (12–17), "evening" (≥ 17:00 and late-night times before the
    day start) or "anytime" (no time)."""
    t = habit_time(habit)
    if t is None:
        return "anytime"
    if t.hour < day_start_hour:
        return "evening"
    if t.hour < 12:
        return "morning"
    if t.hour < 17:
        return "afternoon"
    return "evening"


def sort_key(habit: Habit, *, day_start_hour: int = 0) -> tuple[int, int, int, int]:
    """Order inside the page: group, then time (from the logical day start), then position."""
    t = habit_time(habit)
    group = GROUPS.index(group_of(habit, day_start_hour=day_start_hour))
    if t is None:
        minutes = 0
    else:
        minutes = (t.hour * 60 + t.minute - day_start_hour * 60) % (24 * 60)
    return group, minutes, int(habit.position), int(habit.id)


# --------------------------------------------------------------------------- focus habits


def _norm_project(name: str | None) -> str:
    return (name or "").strip().casefold()


def focused_minutes(sessions: Iterable[Any], project: str | None = None) -> int:
    """Whole focused minutes of counted sessions (completed / ended early), optionally only those
    whose project matches *project* (case-insensitive)."""
    want = _norm_project(project)
    total = 0
    for s in sessions:
        if str(s.outcome) not in COUNTED_OUTCOMES:
            continue
        if want and _norm_project(s.project) != want:
            continue
        total += int(s.focused_seconds or 0)
    return total // 60


def auto_satisfied(habit: Habit, minutes: int) -> bool:
    """True for an auto-complete habit whose focus-minute target is met."""
    return habit.is_auto and int(minutes) >= int(habit.focus_minutes or 0)


# --------------------------------------------------------------------------- summaries


def today_counts(habits: Iterable[Habit], logs: Iterable[HabitLog], today: date) -> tuple[int, int, int]:
    """``(done, skipped, scheduled)`` for the habits scheduled *today*."""
    status = {log.habit_id: str(log.status) for log in logs if log.date == today}
    done = skipped = scheduled = 0
    for h in habits:
        if h.archived or not is_scheduled(h, today):
            continue
        scheduled += 1
        st = status.get(h.id)
        if st == "done":
            done += 1
        elif st == "skipped":
            skipped += 1
    return done, skipped, scheduled


def days_label(mask: int) -> str:
    """"Every day", "Weekdays", "Weekends", "No days" or e.g. "Mon, Wed, Fri"."""
    mask = int(mask) & _ALL_DAYS
    if mask == _ALL_DAYS:
        return "Every day"
    if mask == 0b0011111:
        return "Weekdays"
    if mask == 0b1100000:
        return "Weekends"
    if mask == 0:
        return "No days"
    return ", ".join(name for i, name in enumerate(_WEEKDAY_SHORT) if mask & (1 << i))
