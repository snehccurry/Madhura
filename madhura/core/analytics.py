"""STUB — owned by [I]. Pure analytics aggregations (spec §5.4).

Created by the app shell [F3] with the spec's function names returning empty/zero results of the
right shape; the insights agent replaces this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from madhura.core.days import date_range

__all__ = [
    "daily_totals",
    "weekly_totals",
    "hour_weekday_matrix",
    "focus_score",
    "came_back",
    "top_projects",
    "top_tasks",
    "drift_reasons",
    "period_delta",
    "goal_dates",
    "habit_completion",
    "best_day",
]


def daily_totals(sessions: Iterable[Any], start_d: date, end_d: date, day_start_hour: int) -> list[tuple[date, int]]:
    """(date, focused seconds) for every date in ``[start_d, end_d]``. STUB: all zero."""
    return [(d, 0) for d in date_range(start_d, end_d)]


def weekly_totals(sessions: Iterable[Any], start_week: date, n_weeks: int, day_start_hour: int) -> list[tuple[date, int]]:
    """(week start, focused seconds) for *n_weeks* weeks. STUB: all zero."""
    return [(start_week + timedelta(weeks=i), 0) for i in range(max(0, int(n_weeks)))]


def hour_weekday_matrix(sessions: Iterable[Any]) -> list[list[float]]:
    """7×24 focused seconds (Mon..Sun × hour). STUB: all zero."""
    return [[0.0] * 24 for _ in range(7)]


def focus_score(checkins: Iterable[Any]) -> float | None:
    """focused / (focused + drifted); None without answered check-ins."""
    focused = drifted = 0
    for c in checkins:
        response = str(getattr(c, "response", ""))
        if response == "focused":
            focused += 1
        elif response == "drifted":
            drifted += 1
    total = focused + drifted
    return focused / total if total else None


def came_back(checkins: Iterable[Any]) -> int:
    """Number of 'drifted' answers (framed positively as "came back")."""
    return sum(1 for c in checkins if str(getattr(c, "response", "")) == "drifted")


def top_projects(sessions: Iterable[Any], n: int = 6) -> list[tuple[str, int]]:
    """(project, focused seconds), largest first. STUB: empty."""
    return []


def top_tasks(sessions: Iterable[Any], n: int = 6) -> list[tuple[str, int]]:
    """(task, focused seconds), largest first. STUB: empty."""
    return []


def drift_reasons(checkins: Iterable[Any]) -> list[tuple[str, int]]:
    """(reason, count), most frequent first. STUB: empty."""
    return []


def period_delta(curr: float, prev: float) -> float | None:
    """Relative change of *curr* vs *prev* (None when *prev* is 0)."""
    if not prev:
        return None
    return (float(curr) - float(prev)) / float(prev)


def goal_dates(sessions: Iterable[Any], goal_minutes: int, day_start_hour: int) -> set[date]:
    """Logical dates whose focus met the goal. STUB: empty."""
    return set()


def habit_completion(habits: Sequence[Any], logs: Iterable[Any], start_d: date, end_d: date) -> float | None:
    """Done / scheduled habit-days in the range. STUB: None."""
    return None


def best_day(totals: Iterable[tuple[date, int]]) -> date | None:
    """Date with the largest total (None when all zero)."""
    best: tuple[date, int] | None = None
    for d, secs in totals:
        if secs > 0 and (best is None or secs > best[1]):
            best = (d, secs)
    return best[0] if best else None
