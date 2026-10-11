"""Helpers for the Focus page idle view [P1]: day totals, goal streaks, session captions and the
undo-able pending actions behind "Deleted — Undo" toasts.

Everything that reads time uses ``ctx.today()`` / ``ctx.day_bounds()`` (logical day, spec §1.1).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer

from madhura.core.days import logical_date
from madhura.core.models import Outcome, Session
from madhura.core.streaks import day_streak
from madhura.core.timefmt import fmt_duration

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "STREAK_LOOKBACK_DAYS",
    "totals_by_date",
    "goal_dates",
    "focus_score",
    "score_text",
    "session_caption",
    "today_sessions",
    "goal_streak",
    "UndoQueue",
]

log = logging.getLogger(__name__)

#: How far back the goal streak looks (days).
STREAK_LOOKBACK_DAYS = 120


def totals_by_date(sessions: Iterable[Session], day_start_hour: int) -> dict[date, int]:
    """Focused seconds per logical date of ``started_at`` (counted outcomes only)."""
    totals: dict[date, int] = {}
    for s in sessions:
        if not s.counted:
            continue
        d = logical_date(s.started_at, day_start_hour)
        totals[d] = totals.get(d, 0) + int(s.focused_seconds)
    return totals


def goal_dates(totals: Mapping[date, int], goal_seconds: int) -> set[date]:
    """Dates whose focused total reached *goal_seconds* (a goal of 0 is never "met")."""
    if goal_seconds <= 0:
        return set()
    return {d for d, secs in totals.items() if secs >= goal_seconds}


def focus_score(counts: Mapping[str, int]) -> float | None:
    """focused / (focused + drifted); 'missed' excluded; None without answered check-ins."""
    focused = int(counts.get("focused", 0))
    drifted = int(counts.get("drifted", 0))
    answered = focused + drifted
    if answered <= 0:
        return None
    return focused / answered


def score_text(counts: Mapping[str, int]) -> str | None:
    """``"86% focused"`` or None when no check-in was answered."""
    score = focus_score(counts)
    if score is None:
        return None
    return f"{int(round(score * 100))}% focused"


def session_caption(session: Session, counts: Mapping[str, int]) -> str:
    """Today-card caption: ``"45m · 86% focused"`` (+ ``" · ended early"`` when not completed)."""
    parts = [fmt_duration(session.focused_seconds)]
    score = score_text(counts)
    if score:
        parts.append(score)
    if session.outcome == Outcome.ENDED_EARLY:
        parts.append("ended early")
    return " · ".join(parts)


def today_sessions(ctx: AppContext) -> list[Session]:
    """Counted sessions (completed / ended early) started on the logical today, oldest first."""
    start, end = ctx.day_bounds(ctx.today())
    return ctx.repo.sessions_between(start, end)


def goal_streak(ctx: AppContext, today: date | None = None) -> tuple[int, int]:
    """``(current, best)`` daily-goal streak over the last :data:`STREAK_LOOKBACK_DAYS` days.

    Non-work days are neutral and an unmet today never breaks it (``core.streaks.day_streak``).
    """
    today = today or ctx.today()
    goal_seconds = int(ctx.settings.get("daily_goal_minutes")) * 60
    hour = ctx.day_start_hour()
    first = today - timedelta(days=STREAK_LOOKBACK_DAYS)
    start, _ = ctx.day_bounds(first)
    _, end = ctx.day_bounds(today)
    totals = totals_by_date(ctx.repo.sessions_between(start, end), hour)
    return day_streak(goal_dates(totals, goal_seconds), today, int(ctx.settings.get("work_days")))


# =========================================================================== undo queue


@dataclass(eq=False)
class _Pending:
    commit: Callable[[], Any]
    undo: Callable[[], Any]
    token: Any = None
    timer: QTimer | None = field(default=None, repr=False)


class UndoQueue(QObject):
    """Runs an action now (optimistically, in the UI) and commits it when its "Undo" toast goes.

    ``push(text, commit, undo)`` shows ``text`` with an "Undo" action through
    ``ctx.window.show_toast``. The action is committed when that toast expires — timeout,
    replacement by another toast, or ``MainWindow.flush_toasts()`` on quit — or reverted when Undo
    is clicked. Windows without a ``toasts.expired`` signal (tests' FakeWindowController) commit
    after the toast duration via a timer. :meth:`flush` commits everything still pending.
    """

    UNDO_TEXT = "Undo"

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._pending: list[_Pending] = []
        self._host: Any = None

    def pending_count(self) -> int:
        """Number of actions waiting for their toast to go."""
        return len(self._pending)

    def push(
        self,
        text: str,
        commit: Callable[[], Any],
        undo: Callable[[], Any],
        *,
        kind: str = "info",
        duration_ms: int = 5000,
    ) -> None:
        """Show the undo toast for an action already applied in the UI."""
        window = self._ctx.window
        if window is None:
            self._run(commit)
            return
        entry = _Pending(commit, undo)
        self._pending.append(entry)
        token = window.show_toast(text, kind, self.UNDO_TEXT, lambda e=entry: self._undo(e), duration_ms)
        host = getattr(window, "toasts", None)
        expired = getattr(host, "expired", None)
        if isinstance(token, int) and not isinstance(token, bool) and expired is not None:
            entry.token = token
            if self._host is not host:
                expired.connect(self._on_expired)
                self._host = host
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(1000, int(duration_ms)) + 250)
        timer.timeout.connect(lambda e=entry: self._commit(e))
        entry.timer = timer
        timer.start()

    def flush(self) -> None:
        """Commit every pending action now."""
        for entry in list(self._pending):
            self._commit(entry)

    # ------------------------------------------------------------------ internals

    def _on_expired(self, token: int) -> None:
        for entry in list(self._pending):
            if entry.token == token:
                self._commit(entry)

    def _take(self, entry: _Pending) -> bool:
        if entry not in self._pending:
            return False
        self._pending.remove(entry)
        if entry.timer is not None:
            entry.timer.stop()
            entry.timer.deleteLater()
            entry.timer = None
        return True

    def _commit(self, entry: _Pending) -> None:
        if self._take(entry):
            self._run(entry.commit)

    def _undo(self, entry: _Pending) -> None:
        if self._take(entry):
            self._run(entry.undo)

    def _run(self, fn: Callable[[], Any]) -> None:
        if getattr(self._ctx.repo, "closed", False):
            log.warning("Repository closed; dropping a pending action")
            return
        try:
            fn()
        except Exception:  # noqa: BLE001 - a failed commit/undo is logged, never fatal
            log.exception("Undoable action failed")
