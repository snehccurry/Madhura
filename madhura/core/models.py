"""Domain models (spec §4) and ISO conversion helpers.

Datetimes are naive local and stored as ``"YYYY-MM-DDTHH:MM:SS"``; dates as ``"YYYY-MM-DD"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

__all__ = [
    "ValueEnum",
    "Outcome",
    "CheckInResponse",
    "HabitStatus",
    "COUNTED_OUTCOMES",
    "Session",
    "CheckIn",
    "PlannedTask",
    "Habit",
    "HabitLog",
    "Capture",
    "to_iso",
    "from_iso",
    "to_date_str",
    "from_date_str",
]


class ValueEnum(str, Enum):
    """``str`` enum whose ``str()``/f-string form is its value on every Python version."""

    def __str__(self) -> str:
        return str(self.value)

    def __format__(self, spec: str) -> str:
        return format(str(self.value), spec)


class Outcome(ValueEnum):
    """Session outcome."""

    ACTIVE = "active"
    COMPLETED = "completed"
    ENDED_EARLY = "ended_early"
    ABANDONED = "abandoned"


class CheckInResponse(ValueEnum):
    """Answer to a focus check-in."""

    FOCUSED = "focused"
    DRIFTED = "drifted"
    MISSED = "missed"


class HabitStatus(ValueEnum):
    """Per-day habit log status (absence of a log = not done)."""

    DONE = "done"
    SKIPPED = "skipped"


#: Outcomes that count toward totals, goals, streaks and analytics.
COUNTED_OUTCOMES: tuple[str, ...] = (Outcome.COMPLETED.value, Outcome.ENDED_EARLY.value)


# --------------------------------------------------------------------------- ISO helpers


def to_iso(dt: datetime) -> str:
    """Naive local ISO string with second precision (tz-aware input is converted to local)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat(timespec="seconds")


def from_iso(text: str | None) -> datetime | None:
    """Parse an ISO datetime string (None/empty → None)."""
    if not text:
        return None
    return datetime.fromisoformat(text)


def to_date_str(d: date | datetime) -> str:
    """``YYYY-MM-DD`` for a date (a datetime is reduced to its date)."""
    if isinstance(d, datetime):
        d = d.date()
    return d.isoformat()


def from_date_str(text: str | None) -> date | None:
    """Parse ``YYYY-MM-DD`` (None/empty → None)."""
    if not text:
        return None
    return date.fromisoformat(text[:10])


# --------------------------------------------------------------------------- models


@dataclass
class Session:
    """One focus block (row in ``sessions``)."""

    id: int
    task: str
    project: str | None
    planned_task_id: int | None
    started_at: datetime
    ended_at: datetime | None
    planned_seconds: int
    focused_seconds: int
    paused_seconds: int
    outcome: Outcome
    note: str | None = None

    @property
    def counted(self) -> bool:
        """True when the outcome counts toward totals (completed / ended early)."""
        return self.outcome in (Outcome.COMPLETED, Outcome.ENDED_EARLY)

    @property
    def planned_minutes(self) -> int:
        """Planned length in whole minutes (rounded)."""
        return int(round(self.planned_seconds / 60))


@dataclass
class CheckIn:
    """One check-in answer. ``response`` is a :class:`CheckInResponse` (compares equal to its str)."""

    id: int
    session_id: int
    at: datetime
    response: CheckInResponse
    reason: str | None = None


@dataclass
class PlannedTask:
    """An "Up next" item planned for a date."""

    id: int
    title: str
    project: str | None
    minutes: int
    plan_date: date
    position: int
    done: bool
    created_at: datetime
    completed_at: datetime | None = None


@dataclass
class Habit:
    """A daily habit. ``days`` is a Mon=bit0…Sun=bit6 mask; ``time`` is "HH:MM" or None (anytime)."""

    id: int
    name: str
    emoji: str | None
    cue: str | None
    time: str | None
    days: int
    reminder: bool
    urgent: bool
    focus_minutes: int | None
    project: str | None
    position: int
    archived: bool
    created_at: datetime

    @property
    def is_auto(self) -> bool:
        """True for auto-complete habits (count focus time)."""
        return self.focus_minutes is not None and self.focus_minutes > 0


@dataclass
class HabitLog:
    """Per-day habit status. ``status`` is a :class:`HabitStatus` (compares equal to its str)."""

    id: int
    habit_id: int
    date: date
    status: HabitStatus
    at: datetime


@dataclass
class Capture:
    """A parking-lot capture (stray thought)."""

    id: int
    session_id: int | None
    text: str
    at: datetime
    done: bool
