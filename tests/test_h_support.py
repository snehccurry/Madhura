"""Shared helpers for the habits [H] tests (test_h_*.py). Contains no tests itself."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget
from test_shell_support import advance, dispose_shell, make_shell  # noqa: F401

from madhura.context import AppContext
from madhura.core.clock import FakeClock
from madhura.ui.main_window import MainWindow
from madhura.ui.pages.habits_page import HabitsPage

TODAY = date(2026, 10, 14)  # FakeClock default: Wednesday 10:00
LONG_AGO = datetime(2026, 9, 1, 8, 0)


def pump(n: int = 5) -> None:
    """Process pending events a few times."""
    for _ in range(n):
        QApplication.processEvents()


def win_of(ctx: AppContext) -> MainWindow:
    """The real main window."""
    assert isinstance(ctx.window, MainWindow)
    return ctx.window


def habits_page(ctx: AppContext, *, show: bool = True) -> HabitsPage:
    """Show the main window on the Habits page and return the page."""
    win = win_of(ctx)
    if show:
        win.resize(1120, 760)
        win.show_main(activate=True)
        win.show_page("habits")
        pump(6)
    page = win.pages["habits"]
    assert isinstance(page, HabitsPage)
    return page


def key(widget: QWidget, k: Qt.Key, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, text: str = "") -> None:
    """Send a key press straight to *widget*."""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))


def add_habit(ctx: AppContext, name: str, *, created: datetime = LONG_AGO, **fields: Any) -> int:
    """Add a habit created long ago (so it is due today when its time passes)."""
    hid = ctx.repo.add_habit(name, **fields)
    ctx.repo.update_habit(hid, created_at=created)
    return hid


def log(ctx: AppContext, hid: int, d: date, status: str | None) -> None:
    """Write a habit log."""
    ctx.repo.set_habit_status(hid, d, status, at=datetime.combine(d, datetime.min.time()).replace(hour=12))


def add_session(ctx: AppContext, start: datetime, minutes: int, *, outcome: str = "completed", project: str | None = None) -> int:
    """Insert a finished session row."""
    sid = ctx.repo.create_session("Write", project, None, start, minutes * 60)
    ctx.repo.update_session(sid, focused_seconds=minutes * 60, outcome=outcome, ended_at=start + timedelta(minutes=minutes))
    return sid


def set_now(ctx: AppContext, dt: datetime) -> None:
    """Move the FakeClock (no engine polling — use only while the engine is idle)."""
    clock = ctx.clock
    assert isinstance(clock, FakeClock)
    clock.set(dt)


def hshell_context(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> Iterator[AppContext]:
    """Generator behind each module's ``hshell`` fixture: a running shell (no tray) whose habit
    reminder service has not auto-started its timers (tests drive ``check()`` explicitly)."""
    ctx = make_shell(qapp, data_dir, fake_clock)
    # Check-ins would pause long FakeClock sessions after two "missed" answers; nudges are noise.
    ctx.settings.update(checkin_enabled=False, nudge_mode="off")
    svc = ctx.habit_reminders
    assert svc is not None
    svc._startup.stop()  # noqa: SLF001 - tests drive check() explicitly
    yield ctx
    dispose_shell(ctx, qapp)
