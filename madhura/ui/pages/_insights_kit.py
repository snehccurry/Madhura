"""Data side of the Insights page [I]: one :class:`InsightsSnapshot` per (range, day) built from the
repository with the pure functions in ``core.analytics``, plus small formatting helpers shared by
the Insights page, the weekly recap card and the wrap-up sheet.

Everything time-related uses ``ctx.today()`` / ``ctx.day_start_hour()`` (logical days, spec §1.1).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from madhura.core import analytics as an
from madhura.core.days import ALL_DAYS, logical_date, week_start
from madhura.core.models import CheckIn, Session
from madhura.core.streaks import day_streak
from madhura.core.timefmt import MONTHS_SHORT, WEEKDAYS_SHORT, fmt_date_short, fmt_duration, fmt_time

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "RANGE_KEY",
    "HEATMAP_MIN_SESSIONS",
    "RECENT_LIMIT",
    "InsightsSnapshot",
    "load_snapshot",
    "describe_period",
    "has_any_sessions",
    "bar_label",
    "bar_title",
    "bar_detail",
    "sessions_word",
    "fmt_pct",
    "fmt_session_when",
    "quote",
    "work_day_count",
]

#: kv key remembering the selected Insights range.
RANGE_KEY = "insights_range"
#: The heatmap shows patterns only from this many counted sessions in the range.
HEATMAP_MIN_SESSIONS = 3
#: "Recent sessions" shows at most this many rows.
RECENT_LIMIT = 50

_EPOCH = datetime(1970, 1, 1)
_FAR_FUTURE = datetime(9999, 1, 1)


# =========================================================================== formatting


def sessions_word(n: int) -> str:
    """``"1 session"`` / ``"3 sessions"``."""
    return f"{n} session" if n == 1 else f"{n} sessions"


def fmt_pct(fraction: float | None) -> str:
    """``0.843`` → ``"84%"``; None → ``"—"``."""
    if fraction is None:
        return "—"
    return f"{int(round(fraction * 100))}%"


def quote(text: str, limit: int = 40) -> str:
    """‘Text’ in curly quotes, elided to *limit* characters."""
    text = " ".join((text or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"‘{text}’"


def work_day_count(mask: int) -> int:
    """Number of weekdays set in a Mon=bit0…Sun=bit6 mask."""
    return bin(int(mask) & ALL_DAYS).count("1")


def bar_label(range_key: str, d: date, index: int, count: int) -> str:
    """x-axis label: weekday (7 days), every 5th date counted back from today (30 days),
    the week's start date (12 weeks)."""
    if range_key == "7d":
        return WEEKDAYS_SHORT[d.weekday()]
    if range_key == "30d":
        return f"{d.day} {MONTHS_SHORT[d.month - 1]}" if (count - 1 - index) % 5 == 0 else ""
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def bar_title(range_key: str, d: date) -> str:
    """Callout title: ``"Tue 14 Oct"`` or ``"Week of 13 Oct"``."""
    if range_key == "12w":
        return f"Week of {d.day} {MONTHS_SHORT[d.month - 1]}"
    return fmt_date_short(d)


def bar_detail(seconds: int, sessions: int) -> str:
    """Callout detail: ``"2h 15m · 3 sessions"`` or ``"No focus"``."""
    if seconds <= 0 and sessions <= 0:
        return "No focus"
    return f"{fmt_duration(seconds)} · {sessions_word(sessions)}"


def fmt_session_when(started: datetime, today: date, day_start_hour: int, use_24h: bool) -> str:
    """``"Today, 09:30"`` / ``"Yesterday, 14:05"`` / ``"Tue 7 Oct, 09:30"`` (logical days)."""
    d = logical_date(started, day_start_hour)
    clock = fmt_time(started, use_24h)
    if d == today:
        return f"Today, {clock}"
    if d == today - timedelta(days=1):
        return f"Yesterday, {clock}"
    return f"{fmt_date_short(d)}, {clock}"


# =========================================================================== snapshot


@dataclass
class InsightsSnapshot:
    """Everything the Insights page shows for one range (computed in one go)."""

    range_key: str
    today: date
    period: an.Period
    prev_period: an.Period
    any_sessions: bool
    sessions: list[Session] = field(default_factory=list)
    prev_sessions: list[Session] = field(default_factory=list)
    checkins: list[CheckIn] = field(default_factory=list)
    prev_checkins: list[CheckIn] = field(default_factory=list)
    focus_seconds: int = 0
    prev_focus_seconds: int = 0
    session_count: int = 0
    prev_session_count: int = 0
    score: float | None = None
    prev_score: float | None = None
    answered: int = 0
    came_back: int = 0
    habit_done: int = 0
    habit_scheduled: int = 0
    habit_rate: float | None = None
    prev_habit_rate: float | None = None
    streak: tuple[int, int] = (0, 0)
    buckets: list[an.DayStat] = field(default_factory=list)
    goal_seconds: int = 0
    daily_goal_seconds: int = 0
    matrix: list[list[float]] = field(default_factory=list)
    projects: list[an.Ranked] = field(default_factory=list)
    tasks: list[an.Ranked] = field(default_factory=list)
    drift: list[tuple[str, int]] = field(default_factory=list)
    recent: list[Session] = field(default_factory=list)
    recent_counts: dict[int, dict[str, int]] = field(default_factory=dict)

    @property
    def enough_for_heatmap(self) -> bool:
        """At least :data:`HEATMAP_MIN_SESSIONS` counted sessions in the range."""
        return len(self.sessions) >= HEATMAP_MIN_SESSIONS

    @property
    def focus_delta(self) -> float | None:
        """Relative change of focus time vs the previous period."""
        return an.period_delta(self.focus_seconds, self.prev_focus_seconds)

    @property
    def sessions_delta(self) -> float | None:
        """Relative change of the session count."""
        return an.period_delta(self.session_count, self.prev_session_count)

    @property
    def score_delta(self) -> float | None:
        """Difference of focus scores (percentage points as a fraction), None if either is missing."""
        if self.score is None or self.prev_score is None:
            return None
        return self.score - self.prev_score

    @property
    def habit_delta(self) -> float | None:
        """Difference of habit completion rates, None if either is missing."""
        if self.habit_rate is None or self.prev_habit_rate is None:
            return None
        return self.habit_rate - self.prev_habit_rate


def has_any_sessions(ctx: AppContext) -> bool:
    """True once any finished session (any outcome) exists."""
    try:
        return bool(ctx.repo.recent_sessions(limit=1))
    except sqlite3.Error:
        return False


def _without(sessions: Iterable[Session], exclude: set[int]) -> list[Session]:
    return [s for s in sessions if s.id not in exclude]


def _checkins_for(ctx: AppContext, sessions: list[Session]) -> dict[int, list[CheckIn]]:
    out: dict[int, list[CheckIn]] = {}
    if not sessions:
        return out
    ids = {s.id for s in sessions}
    first = min(s.started_at for s in sessions)
    for c in ctx.repo.checkins_between(first, _FAR_FUTURE):
        if c.session_id in ids:
            out.setdefault(c.session_id, []).append(c)
    return out


def load_snapshot(ctx: AppContext, range_key: str, exclude: Iterable[int] = ()) -> InsightsSnapshot:
    """Build the :class:`InsightsSnapshot` for *range_key* ("7d" | "30d" | "12w") as of
    ``ctx.today()``. Sessions in *exclude* (pending undo-deletes) are left out everywhere."""
    if range_key not in an.RANGES:
        range_key = "7d"
    excluded = set(exclude)
    repo = ctx.repo
    today = ctx.today()
    hour = ctx.day_start_hour()
    settings = ctx.settings
    period = an.range_period(range_key, today)
    prev = an.previous_range_period(range_key, today)
    start, end = an.period_bounds(period, hour)
    pstart, pend = an.period_bounds(prev, hour)
    snap = InsightsSnapshot(range_key, today, period, prev, any_sessions=has_any_sessions(ctx))

    snap.sessions = _without(repo.sessions_between(start, end), excluded)
    snap.prev_sessions = _without(repo.sessions_between(pstart, pend), excluded)
    by_session = _checkins_for(ctx, snap.prev_sessions + snap.sessions)
    snap.checkins = [c for s in snap.sessions for c in by_session.get(s.id, [])]
    snap.prev_checkins = [c for s in snap.prev_sessions for c in by_session.get(s.id, [])]

    snap.focus_seconds = an.total_seconds(snap.sessions)
    snap.prev_focus_seconds = an.total_seconds(snap.prev_sessions)
    snap.session_count = len(an.counted(snap.sessions))
    snap.prev_session_count = len(an.counted(snap.prev_sessions))
    counts = an.checkin_counts(snap.checkins)
    snap.answered = counts["focused"] + counts["drifted"]
    snap.score = an.focus_score(snap.checkins)
    snap.prev_score = an.focus_score(snap.prev_checkins)
    snap.came_back = counts["drifted"]
    snap.drift = an.drift_reasons(snap.checkins)

    habits = repo.habits()
    logs = repo.habit_logs_between(prev.start, period.end)
    snap.habit_done, snap.habit_scheduled = an.habit_completion_counts(habits, logs, period.start, period.end, today=today)
    snap.habit_rate = snap.habit_done / snap.habit_scheduled if snap.habit_scheduled else None
    snap.prev_habit_rate = an.habit_completion(habits, logs, prev.start, prev.end, today=today)

    goal_minutes = int(settings.get("daily_goal_minutes"))
    work_days = int(settings.get("work_days"))
    history = _without(repo.sessions_between(_EPOCH, end), excluded)
    snap.streak = day_streak(an.goal_dates(history, goal_minutes, hour), today, work_days)

    snap.daily_goal_seconds = goal_minutes * 60
    if range_key == "12w":
        snap.buckets = an.weekly_stats(snap.sessions, week_start(period.start), 12, hour)
        snap.goal_seconds = snap.daily_goal_seconds * work_day_count(work_days)
    else:
        snap.buckets = an.daily_stats(snap.sessions, period.start, period.end, hour)
        snap.goal_seconds = snap.daily_goal_seconds

    snap.matrix = an.hour_weekday_matrix(snap.sessions)
    snap.projects = an.ranked(snap.sessions, "project", 6)
    snap.tasks = an.ranked(snap.sessions, "task", 6)

    recent = _without(repo.recent_sessions(limit=RECENT_LIMIT + len(excluded), start_dt=start, end_dt=end), excluded)
    snap.recent = recent[:RECENT_LIMIT]
    recent_checkins = _checkins_for(ctx, snap.recent)
    snap.recent_counts = {s.id: an.checkin_counts(recent_checkins.get(s.id, [])) for s in snap.recent}
    return snap


def describe_period(snap: InsightsSnapshot) -> str:
    """Accessible summary line: ``"Last 7 days: 11h 20m focused in 9 sessions."``."""
    label = an.RANGE_LABELS.get(snap.range_key, "")
    return f"Last {label}: {fmt_duration(snap.focus_seconds)} focused in {sessions_word(snap.session_count)}."
