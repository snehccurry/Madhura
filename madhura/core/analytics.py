"""Pure analytics aggregations for Insights, the weekly recap and the wrap-up (spec §1.9, §5.4).

No Qt, no database and no wall clock: callers pass model objects (``core.models``) and dates
(``ctx.today()`` — the *logical* date).

Counting rule (shared everywhere): totals, goals, streaks, the heatmap and project/task rankings
use ``focused_seconds`` of sessions whose outcome is *completed* or *ended early* only. Every
aggregation here filters with :func:`counted`, so passing abandoned/active rows is harmless.

Day buckets: a session belongs to the logical date of its ``started_at``
(``core.days.logical_date(started_at, day_start_hour)``). The hour × weekday heatmap is the
exception — it is about clock hours, so it uses wall-clock weekday/hour and splits a session's
focus across the hour boundaries it spans.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from madhura.core.days import date_range, logical_date, week_start

__all__ = [
    "DRIFT_REASON_ORDER",
    "RANGES",
    "RANGE_LABELS",
    "DayStat",
    "Period",
    "Ranked",
    "best_day",
    "came_back",
    "checkin_counts",
    "counted",
    "daily_stats",
    "daily_totals",
    "drift_reasons",
    "focus_score",
    "goal_dates",
    "habit_completion",
    "habit_completion_counts",
    "habit_day_counts",
    "heatmap_levels",
    "hour_weekday_matrix",
    "matrix_has_early_hours",
    "nice_ticks",
    "period_bounds",
    "period_delta",
    "previous_period",
    "previous_range_period",
    "range_period",
    "ranked",
    "top_projects",
    "top_tasks",
    "total_seconds",
    "weekly_stats",
    "weekly_totals",
]

#: Insights ranges, in the order of the segmented control.
RANGES: tuple[str, ...] = ("7d", "30d", "12w")
#: Range key → human label (used in "vs previous 7 days").
RANGE_LABELS: dict[str, str] = {"7d": "7 days", "30d": "30 days", "12w": "12 weeks"}

#: The check-in popup's reason chips, in display order (ties in :func:`drift_reasons` follow it).
DRIFT_REASON_ORDER: tuple[str, ...] = ("Phone", "Social media", "Messages", "Email", "Browsing", "Daydreaming", "Other")

_COUNTED = ("completed", "ended_early")


# --------------------------------------------------------------------------- basics


def _str(value: Any) -> str:
    """Enum-or-str → plain value string."""
    return str(getattr(value, "value", value) or "")


def counted(sessions: Iterable[Any]) -> list[Any]:
    """Sessions whose outcome counts toward totals (completed / ended early), in input order."""
    return [s for s in sessions if _str(getattr(s, "outcome", "")) in _COUNTED]


def total_seconds(sessions: Iterable[Any]) -> int:
    """Sum of ``focused_seconds`` of the counted sessions."""
    return int(sum(max(0, int(s.focused_seconds)) for s in counted(sessions)))


# --------------------------------------------------------------------------- periods


@dataclass(frozen=True)
class Period:
    """An inclusive range of logical dates (``start`` … ``end``)."""

    start: date
    end: date

    @property
    def days(self) -> int:
        """Number of dates in the period."""
        return (self.end - self.start).days + 1

    def contains(self, d: date) -> bool:
        """True when *d* lies in the period."""
        return self.start <= d <= self.end


def range_period(range_key: str, today: date) -> Period:
    """The logical dates covered by an Insights range ending *today*.

    ``"7d"`` → the last 7 days, ``"30d"`` → the last 30 days, ``"12w"`` → the current ISO week
    (up to today) plus the 11 weeks before it.
    """
    if range_key == "30d":
        return Period(today - timedelta(days=29), today)
    if range_key == "12w":
        return Period(week_start(today) - timedelta(weeks=11), today)
    return Period(today - timedelta(days=6), today)


def previous_period(period: Period, *, whole_weeks: bool = False) -> Period:
    """The equally long period before *period* (for "vs previous …" deltas).

    By default it ends the day before *period* starts. With ``whole_weeks`` (the 12-week range,
    which ends mid-week) the same number of days is shifted back by whole weeks instead, so a
    partial current week is compared with the same weekdays of the earlier week (like with like).
    """
    length = period.days
    shift = timedelta(days=((length + 6) // 7) * 7 if whole_weeks else length)
    return Period(period.start - shift, period.end - shift)


def previous_range_period(range_key: str, today: date) -> Period:
    """:func:`previous_period` of :func:`range_period` (whole weeks for ``"12w"``)."""
    return previous_period(range_period(range_key, today), whole_weeks=range_key == "12w")


def period_bounds(period: Period, day_start_hour: int) -> tuple[datetime, datetime]:
    """Naive local ``[start, end)`` datetimes of *period* (logical day boundaries)."""
    start = datetime.combine(period.start, datetime.min.time()) + timedelta(hours=day_start_hour)
    end = datetime.combine(period.end, datetime.min.time()) + timedelta(days=1, hours=day_start_hour)
    return start, end


# --------------------------------------------------------------------------- day / week buckets


@dataclass(frozen=True)
class DayStat:
    """Focused seconds and counted-session count for one bucket (a day or a week)."""

    day: date
    seconds: int
    sessions: int


def daily_stats(sessions: Iterable[Any], start_d: date, end_d: date, day_start_hour: int) -> list[DayStat]:
    """One :class:`DayStat` per logical date in ``[start_d, end_d]`` (zero-filled)."""
    secs: dict[date, int] = {}
    count: dict[date, int] = {}
    for s in counted(sessions):
        d = logical_date(s.started_at, day_start_hour)
        if start_d <= d <= end_d:
            secs[d] = secs.get(d, 0) + max(0, int(s.focused_seconds))
            count[d] = count.get(d, 0) + 1
    return [DayStat(d, secs.get(d, 0), count.get(d, 0)) for d in date_range(start_d, end_d)]


def daily_totals(sessions: Iterable[Any], start_d: date, end_d: date, day_start_hour: int) -> list[tuple[date, int]]:
    """``(date, focused seconds)`` for every logical date in ``[start_d, end_d]``."""
    return [(st.day, st.seconds) for st in daily_stats(sessions, start_d, end_d, day_start_hour)]


def weekly_stats(sessions: Iterable[Any], start_week: date, n_weeks: int, day_start_hour: int) -> list[DayStat]:
    """One :class:`DayStat` per ISO week (keyed by its Monday) for *n_weeks* weeks from *start_week*."""
    first = week_start(start_week)
    weeks = [first + timedelta(weeks=i) for i in range(max(0, int(n_weeks)))]
    secs = dict.fromkeys(weeks, 0)
    count = dict.fromkeys(weeks, 0)
    for s in counted(sessions):
        wk = week_start(logical_date(s.started_at, day_start_hour))
        if wk in secs:
            secs[wk] += max(0, int(s.focused_seconds))
            count[wk] += 1
    return [DayStat(w, secs[w], count[w]) for w in weeks]


def weekly_totals(sessions: Iterable[Any], start_week: date, n_weeks: int, day_start_hour: int) -> list[tuple[date, int]]:
    """``(week start, focused seconds)`` for *n_weeks* ISO weeks starting at *start_week*'s Monday."""
    return [(st.day, st.seconds) for st in weekly_stats(sessions, start_week, n_weeks, day_start_hour)]


def best_day(totals: Iterable[tuple[date, int]]) -> date | None:
    """Date with the largest total (the earliest on ties; None when every total is zero)."""
    best: tuple[date, int] | None = None
    for d, secs in totals:
        if secs > 0 and (best is None or secs > best[1]):
            best = (d, secs)
    return best[0] if best else None


# --------------------------------------------------------------------------- heatmap


def hour_weekday_matrix(sessions: Iterable[Any]) -> list[list[float]]:
    """7×24 focused seconds (rows Mon..Sun × clock hours 0..23).

    Each counted session's focus is laid out from ``started_at`` for ``focused_seconds`` and split
    across the hour (and midnight) boundaries it crosses. Pauses are assumed to come after the
    focus, so time away (e.g. a laptop asleep overnight) is never credited to hours you weren't
    working.
    """
    matrix = [[0.0] * 24 for _ in range(7)]
    for s in counted(sessions):
        remaining = float(max(0, int(s.focused_seconds)))
        t: datetime = s.started_at
        while remaining > 1e-9:
            next_hour = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            chunk = min(remaining, (next_hour - t).total_seconds())
            matrix[t.weekday()][t.hour] += chunk
            remaining -= chunk
            t = next_hour
    return matrix


def matrix_has_early_hours(matrix: Sequence[Sequence[float]], min_seconds: float = 60.0) -> bool:
    """True when at least a minute of focus falls in 00:00–05:59 (the heatmap then shows all 24 hours)."""
    return sum(row[h] for row in matrix for h in range(min(6, len(row)))) >= min_seconds


def heatmap_levels(matrix: Sequence[Sequence[float]]) -> list[list[int]]:
    """Quantize to levels 0..4: 0 = no focus, 1..4 = quarters of the busiest cell (ceil)."""
    peak = max((v for row in matrix for v in row), default=0.0)
    out: list[list[int]] = []
    for row in matrix:
        levels = []
        for v in row:
            if v <= 0 or peak <= 0:
                levels.append(0)
            else:
                levels.append(max(1, min(4, int(-(-4 * v // peak)))))
        out.append(levels)
    return out


# --------------------------------------------------------------------------- check-ins


def checkin_counts(checkins: Iterable[Any]) -> dict[str, int]:
    """``{"focused": n, "drifted": n, "missed": n}``."""
    counts = {"focused": 0, "drifted": 0, "missed": 0}
    for c in checkins:
        response = _str(getattr(c, "response", ""))
        if response in counts:
            counts[response] += 1
    return counts


def focus_score(checkins: Iterable[Any]) -> float | None:
    """focused / (focused + drifted) — 'missed' excluded; None without answered check-ins."""
    counts = checkin_counts(checkins)
    answered = counts["focused"] + counts["drifted"]
    return counts["focused"] / answered if answered else None


def came_back(checkins: Iterable[Any]) -> int:
    """Number of 'drifted' answers (framed positively: you noticed and came back)."""
    return checkin_counts(checkins)["drifted"]


def drift_reasons(checkins: Iterable[Any]) -> list[tuple[str, int]]:
    """``(reason, count)`` of 'drifted' answers that named a reason, most frequent first.

    Ties follow the popup's chip order, then the name. Drifts without a reason are not listed
    (they still count in :func:`came_back`).
    """
    counts: dict[str, int] = {}
    for c in checkins:
        if _str(getattr(c, "response", "")) != "drifted":
            continue
        reason = (getattr(c, "reason", None) or "").strip()
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    order = {name: i for i, name in enumerate(DRIFT_REASON_ORDER)}
    return sorted(counts.items(), key=lambda kv: (-kv[1], order.get(kv[0], len(order)), kv[0].lower()))


# --------------------------------------------------------------------------- rankings


@dataclass(frozen=True)
class Ranked:
    """One ranked project or task: display label (None = "No project"), focus, session count and
    the project most of that focus was logged under (for colouring tasks)."""

    label: str | None
    seconds: int
    sessions: int
    project: str | None


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def ranked(sessions: Iterable[Any], by: str = "project", n: int = 6) -> list[Ranked]:
    """Top *n* projects (``by="project"``) or tasks (``by="task"``) by focused seconds.

    Names are merged case- and whitespace-insensitively; the spelling with the most focus wins.
    Sessions without a project are grouped under ``label=None``. Ties: more sessions, then name.
    """
    groups: dict[str, dict[str, Any]] = {}
    for s in counted(sessions):
        raw = s.project if by == "project" else s.task
        text = (raw or "").strip() or None
        key = _norm(text)
        secs = max(0, int(s.focused_seconds))
        g = groups.setdefault(key, {"seconds": 0, "sessions": 0, "spelling": {}, "projects": {}})
        g["seconds"] += secs
        g["sessions"] += 1
        if text is not None:
            g["spelling"][text] = g["spelling"].get(text, 0) + secs + 1
        project = (s.project or "").strip() or None
        g["projects"][project] = g["projects"].get(project, 0) + secs + 1
    items: list[Ranked] = []
    for key, g in groups.items():
        label = max(g["spelling"].items(), key=lambda kv: (kv[1], kv[0]))[0] if key else None
        project = max(g["projects"].items(), key=lambda kv: (kv[1], kv[0] or ""))[0]
        if by == "project":
            project = label
        items.append(Ranked(label, g["seconds"], g["sessions"], project))
    items.sort(key=lambda r: (-r.seconds, -r.sessions, r.label is None, _norm(r.label)))
    return items[: max(0, int(n))]


def top_projects(sessions: Iterable[Any], n: int = 6) -> list[tuple[str | None, int]]:
    """``(project, focused seconds)``, largest first (None = sessions without a project)."""
    return [(r.label, r.seconds) for r in ranked(sessions, "project", n)]


def top_tasks(sessions: Iterable[Any], n: int = 6) -> list[tuple[str, int]]:
    """``(task, focused seconds)``, largest first."""
    return [(r.label or "", r.seconds) for r in ranked(sessions, "task", n)]


# --------------------------------------------------------------------------- deltas & goals


def period_delta(curr: float | None, prev: float | None) -> float | None:
    """Relative change of *curr* vs *prev* (``0.12`` = +12%); None when there is nothing to
    compare against (*prev* missing or zero)."""
    if curr is None or prev is None or prev <= 0:
        return None
    return (float(curr) - float(prev)) / float(prev)


def goal_dates(sessions: Iterable[Any], goal_minutes: int, day_start_hour: int) -> set[date]:
    """Logical dates whose counted focus reached *goal_minutes* (a goal ≤ 0 is never met)."""
    goal = int(goal_minutes) * 60
    if goal <= 0:
        return set()
    totals: dict[date, int] = {}
    for s in counted(sessions):
        d = logical_date(s.started_at, day_start_hour)
        totals[d] = totals.get(d, 0) + max(0, int(s.focused_seconds))
    return {d for d, secs in totals.items() if secs >= goal}


# --------------------------------------------------------------------------- habits


def _scheduled(habit: Any, d: date) -> bool:
    return bool(int(getattr(habit, "days", 0) or 0) & 0b1111111 & (1 << d.weekday()))


def _created(habit: Any) -> date | None:
    created = getattr(habit, "created_at", None)
    if isinstance(created, datetime):
        return created.date()
    return created if isinstance(created, date) else None


def habit_completion_counts(
    habits: Sequence[Any],
    logs: Iterable[Any],
    start_d: date,
    end_d: date,
    *,
    today: date | None = None,
) -> tuple[int, int]:
    """``(done, scheduled)`` habit-days in ``[start_d, end_d]``.

    Only scheduled days on/after a habit's creation count; 'skipped' days are neutral (left out
    of both numbers); archived habits are ignored. *today* (and later dates) only count once a
    habit is done — an unfinished today never lowers the rate.
    """
    status: dict[tuple[int, date], str] = {}
    for log in logs:
        status[(int(log.habit_id), log.date)] = _str(log.status)
    done = scheduled = 0
    for h in habits:
        if getattr(h, "archived", False):
            continue
        created = _created(h)
        first = max(start_d, created) if created is not None else start_d
        for d in date_range(first, end_d):
            if not _scheduled(h, d):
                continue
            st = status.get((int(h.id), d))
            if st == "skipped":
                continue
            if today is not None and d >= today and st != "done":
                continue
            scheduled += 1
            if st == "done":
                done += 1
    return done, scheduled


def habit_completion(
    habits: Sequence[Any],
    logs: Iterable[Any],
    start_d: date,
    end_d: date,
    *,
    today: date | None = None,
) -> float | None:
    """Done / scheduled habit-days in the range (see :func:`habit_completion_counts`); None when
    nothing was scheduled."""
    done, scheduled = habit_completion_counts(habits, logs, start_d, end_d, today=today)
    return done / scheduled if scheduled else None


def habit_day_counts(habits: Sequence[Any], logs: Iterable[Any], d: date) -> tuple[int, int]:
    """``(done, scheduled)`` for one day, as the Habits page counts it (skipped stay scheduled)."""
    status = {int(log.habit_id): _str(log.status) for log in logs if log.date == d}
    done = scheduled = 0
    for h in habits:
        if getattr(h, "archived", False) or not _scheduled(h, d):
            continue
        scheduled += 1
        if status.get(int(h.id)) == "done":
            done += 1
    return done, scheduled


# --------------------------------------------------------------------------- axis ticks

_TICK_STEPS_MIN: tuple[int, ...] = (
    15, 20, 30, 60, 90, 120, 180, 240, 300, 360, 480, 600, 720, 900, 1200, 1800, 2400, 3000, 3600,
)


def nice_ticks(max_seconds: float, count: int = 4) -> list[int]:
    """*count* evenly spaced "nice" tick values in seconds, starting at 0, whose top is ≥ the max.

    Steps are friendly durations (15 min, 30 min, 1 h, 2 h, …); at least 15 minutes per step.
    """
    count = max(2, int(count))
    top = max(0.0, float(max_seconds))
    for step_min in _TICK_STEPS_MIN:
        if step_min * 60 * (count - 1) >= top:
            return [i * step_min * 60 for i in range(count)]
    step = _TICK_STEPS_MIN[-1] * 60
    while step * (count - 1) < top:
        step *= 2
    return [i * step for i in range(count)]
