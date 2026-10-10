"""Shared fixtures/helpers for the Focus page [P1] tests (test_p1_*.py). Contains no tests itself."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from madhura.context import AppContext
from madhura.ui.main_window import MainWindow
from madhura.ui.overlays import session_complete
from madhura.ui.pages.focus_page import FocusPage
from test_shell_support import advance, dispose_shell, make_shell  # noqa: F401

TODAY = date(2026, 10, 14)  # FakeClock default (Wednesday 10:00)


def pump(n: int = 5) -> None:
    """Process pending events a few times."""
    for _ in range(n):
        QApplication.processEvents()


def win_of(ctx: AppContext) -> MainWindow:
    """The real main window of a shell context."""
    assert isinstance(ctx.window, MainWindow)
    return ctx.window


def page_of(ctx: AppContext) -> FocusPage:
    """The Focus page of a shell context."""
    page = win_of(ctx).pages["focus"]
    assert isinstance(page, FocusPage)
    return page


def show(ctx: AppContext, width: int = 1120, height: int = 760) -> MainWindow:
    """Show + activate the main window at a size and let layouts settle."""
    win = win_of(ctx)
    win.resize(width, height)
    win.show_main(activate=True)
    pump(8)
    return win


def key(widget: QWidget, k: Qt.Key, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, text: str = "") -> None:
    """Send a key press directly to ``widget`` (works offscreen without window activation)."""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))


def add_session(
    ctx: AppContext,
    task: str,
    start: datetime,
    focused_min: int,
    outcome: str = "completed",
    project: str | None = None,
    planned_min: int | None = None,
    checkins: tuple[str, ...] = (),
) -> int:
    """Insert a finished session row (+ check-ins)."""
    repo = ctx.repo
    sid = repo.create_session(task, project, None, start, (planned_min or focused_min) * 60)
    repo.update_session(
        sid,
        focused_seconds=focused_min * 60,
        outcome=outcome,
        ended_at=start + timedelta(minutes=focused_min),
    )
    for i, response in enumerate(checkins):
        repo.add_checkin(sid, start + timedelta(minutes=7 * (i + 1)), response)
    return sid


@pytest.fixture
def no_summary(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Replace present_session_complete with a recorder (keeps P2's sheet out of P1 tests)."""
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(session_complete, "present_session_complete", lambda *a: calls.append(a))
    monkeypatch.setattr(session_complete, "show_pending_complete", lambda *_a: None)
    return calls


@pytest.fixture
def focus(qapp: QApplication, data_dir: Path, fake_clock: Any, no_summary: list[Any]) -> Iterator[AppContext]:
    """A running shell (no tray) with the window shown on the Focus page; summaries recorded."""
    ctx = make_shell(qapp, data_dir, fake_clock)
    ctx.settings.update(after_start="stay", time_format="24h")
    show(ctx)
    yield ctx
    dispose_shell(ctx, qapp)


@pytest.fixture
def tray_focus(qapp: QApplication, data_dir: Path, fake_clock: Any, no_summary: list[Any]) -> Iterator[AppContext]:
    """Like :func:`focus` but with a (forced) tray, so hide_main() really hides."""
    ctx = make_shell(qapp, data_dir, fake_clock, tray=True)
    ctx.settings.update(time_format="24h")
    show(ctx)
    yield ctx
    dispose_shell(ctx, qapp)
