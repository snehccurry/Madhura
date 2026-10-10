"""Focus page idle helpers [P1]: totals, goal dates/streak, captions, UndoQueue."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from madhura.context import FakeWindowController
from madhura.core.models import Outcome, Session
from madhura.ui.pages.focus._idle_helpers import (
    UndoQueue,
    focus_score,
    goal_dates,
    goal_streak,
    score_text,
    session_caption,
    today_sessions,
    totals_by_date,
)


def _session(start: datetime, focused_min: int, outcome: Outcome = Outcome.COMPLETED, sid: int = 1) -> Session:
    return Session(sid, "Task", None, None, start, start + timedelta(minutes=focused_min), focused_min * 60,
                   focused_min * 60, 0, outcome)


def test_focus_score_and_text() -> None:
    assert focus_score({"focused": 0, "drifted": 0, "missed": 3}) is None
    assert score_text({"focused": 0, "drifted": 0, "missed": 3}) is None
    assert focus_score({"focused": 6, "drifted": 1, "missed": 5}) == 6 / 7
    assert score_text({"focused": 6, "drifted": 1, "missed": 5}) == "86% focused"
    assert score_text({"focused": 0, "drifted": 2}) == "0% focused"
    assert score_text({"focused": 2, "drifted": 1}) == "67% focused"


def test_session_caption() -> None:
    s = _session(datetime(2026, 10, 14, 9, 0), 45)
    assert session_caption(s, {"focused": 6, "drifted": 1, "missed": 0}) == "45m · 86% focused"
    assert session_caption(s, {"focused": 0, "drifted": 0, "missed": 2}) == "45m"
    early = _session(datetime(2026, 10, 14, 9, 0), 38, Outcome.ENDED_EARLY)
    assert session_caption(early, {}) == "38m · ended early"
    long = _session(datetime(2026, 10, 14, 9, 0), 135)
    assert session_caption(long, {"focused": 1, "drifted": 0}) == "2h 15m · 100% focused"


def test_totals_by_logical_date_counted_only() -> None:
    sessions = [
        _session(datetime(2026, 10, 14, 9, 0), 60),
        _session(datetime(2026, 10, 14, 23, 30), 30),
        _session(datetime(2026, 10, 15, 2, 0), 20),  # before 04:00 → still Oct 14
        _session(datetime(2026, 10, 15, 5, 0), 15),
        _session(datetime(2026, 10, 14, 11, 0), 50, Outcome.ABANDONED),
        _session(datetime(2026, 10, 14, 12, 0), 25, Outcome.ACTIVE),
    ]
    totals = totals_by_date(sessions, 4)
    assert totals == {date(2026, 10, 14): 110 * 60, date(2026, 10, 15): 15 * 60}
    assert totals_by_date(sessions, 0)[date(2026, 10, 15)] == 35 * 60


def test_goal_dates() -> None:
    totals = {date(2026, 10, 13): 7200, date(2026, 10, 14): 7199}
    assert goal_dates(totals, 7200) == {date(2026, 10, 13)}
    assert goal_dates(totals, 0) == set()


class _Settings:
    def __init__(self, **values: Any) -> None:
        self.values = {"daily_goal_minutes": 120, "work_days": 31, "day_start_hour": 4, **values}

    def get(self, key: str) -> Any:
        return self.values[key]


def test_goal_streak_and_today_sessions(repo, fake_clock) -> None:  # noqa: ANN001
    from madhura.context import AppContext

    ctx = AppContext(app=None, clock=fake_clock, repo=repo, settings=_Settings(), theme=None, engine=None,  # type: ignore[arg-type]
                     sound=None, notifier=None, events=None, attention=None, data_dir=Path("."), tray_available=False)
    assert goal_streak(ctx) == (0, 0)

    def add(d: datetime, minutes: int, outcome: str = "completed") -> None:
        sid = repo.create_session("Deep", None, None, d, minutes * 60)
        repo.update_session(sid, focused_seconds=minutes * 60, outcome=outcome)

    add(datetime(2026, 10, 13, 9, 0), 120)  # Tue met
    add(datetime(2026, 10, 12, 9, 0), 60)
    add(datetime(2026, 10, 12, 14, 0), 60)  # Mon met (two sessions)
    add(datetime(2026, 10, 11, 9, 0), 10)  # Sun, unmet, non-work → neutral
    add(datetime(2026, 10, 9, 9, 0), 130)  # Fri met
    add(datetime(2026, 10, 8, 9, 0), 130, "abandoned")  # Thu: abandoned doesn't count → breaks
    assert goal_streak(ctx) == (3, 3)
    add(datetime(2026, 10, 14, 8, 0), 125)  # today met → +1
    assert goal_streak(ctx) == (4, 4)
    assert [s.focused_seconds for s in today_sessions(ctx)] == [125 * 60]
    ctx.settings.values["daily_goal_minutes"] = 150
    assert goal_streak(ctx) == (0, 0)


def test_undo_queue_fallback_timer_commits(qtbot, repo) -> None:  # noqa: ANN001
    from madhura.context import AppContext

    fake = FakeWindowController()
    ctx = AppContext(app=None, clock=None, repo=repo, settings=None, theme=None, engine=None,  # type: ignore[arg-type]
                     sound=None, notifier=None, events=None, attention=None, data_dir=Path("."), tray_available=False)
    ctx.window = fake  # type: ignore[assignment]
    q = UndoQueue(ctx)
    done: list[str] = []
    q.push("Deleted", lambda: done.append("commit"), lambda: done.append("undo"), duration_ms=1000)
    assert fake.toasts == [("Deleted", "info", "Undo")] and q.pending_count() == 1
    timer = q._pending[0].timer  # noqa: SLF001 - commits a little after the toast would expire
    assert timer is not None and timer.isActive() and timer.interval() == 1250
    timer.start(10)  # don't wait the real 1.25 s
    qtbot.waitUntil(lambda: done == ["commit"], timeout=3000)
    assert q.pending_count() == 0
    q.push("Again", lambda: done.append("commit2"), lambda: done.append("undo2"))
    fake.toast_actions[-1]()  # Undo
    assert done[-1] == "undo2" and q.pending_count() == 0
    fake.toast_actions[-1]()  # a second click is a no-op
    assert done.count("undo2") == 1
    q.push("Third", lambda: done.append("commit3"), lambda: done.append("undo3"))
    q.flush()
    assert done[-1] == "commit3"


def test_undo_queue_without_window_commits_now(repo) -> None:  # noqa: ANN001
    from madhura.context import AppContext

    ctx = AppContext(app=None, clock=None, repo=repo, settings=None, theme=None, engine=None,  # type: ignore[arg-type]
                     sound=None, notifier=None, events=None, attention=None, data_dir=Path("."), tray_available=False)
    q = UndoQueue(ctx)
    done: list[str] = []
    q.push("x", lambda: done.append("commit"), lambda: done.append("undo"))
    assert done == ["commit"]


def test_undo_queue_skips_when_repo_closed(repo) -> None:  # noqa: ANN001
    from madhura.context import AppContext

    fake = FakeWindowController()
    ctx = AppContext(app=None, clock=None, repo=repo, settings=None, theme=None, engine=None,  # type: ignore[arg-type]
                     sound=None, notifier=None, events=None, attention=None, data_dir=Path("."), tray_available=False)
    ctx.window = fake  # type: ignore[assignment]
    q = UndoQueue(ctx)
    done: list[str] = []
    q.push("x", lambda: done.append("commit"), lambda: done.append("undo"))
    repo.close()
    q.flush()
    assert done == []
