"""Shared fixtures/helpers for the insights & reflection [I] tests (test_i_*.py). Contains no tests."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from madhura.context import AppContext
from madhura.ui.main_window import MainWindow
from madhura.ui.overlays import session_complete
from test_shell_support import dispose_shell, make_shell

TODAY = date(2026, 10, 14)  # FakeClock default: Wednesday 10:00
REPO_ROOT = Path(__file__).resolve().parent.parent


def pump(n: int = 6) -> None:
    """Process pending events a few times."""
    for _ in range(n):
        QApplication.processEvents()


def key(widget: QWidget, k: Qt.Key, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, text: str = "") -> None:
    """Send a key press straight to *widget*."""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))


def win_of(ctx: AppContext) -> MainWindow:
    """The real main window."""
    assert isinstance(ctx.window, MainWindow)
    return ctx.window


def show(ctx: AppContext, page: str = "insights", width: int = 1120, height: int = 760) -> MainWindow:
    """Show the window on *page* and let layouts settle."""
    win = win_of(ctx)
    win.resize(width, height)
    win.show_main(activate=True)
    win.show_page(page)
    pump(8)
    return win


def add_session(
    ctx_or_repo: Any,
    start: datetime,
    focused_min: float,
    outcome: str = "completed",
    *,
    task: str = "Write the report",
    project: str | None = None,
    checkins: tuple[str | tuple[str, str], ...] = (),
    note: str | None = None,
) -> int:
    """Insert a finished session (+ check-ins every 7 minutes; ("drifted", "Phone") adds a reason)."""
    repo = getattr(ctx_or_repo, "repo", ctx_or_repo)
    secs = int(round(focused_min * 60))
    sid = repo.create_session(task, project, None, start, secs)
    repo.update_session(sid, focused_seconds=secs, outcome=outcome, ended_at=start + timedelta(seconds=secs), note=note)
    for i, item in enumerate(checkins):
        response, reason = (item, None) if isinstance(item, str) else item
        repo.add_checkin(sid, start + timedelta(minutes=7 * (i + 1)), response, reason)
    return sid


def load_seed_demo() -> ModuleType:
    """Import tools/seed_demo.py (not a package)."""
    spec = importlib.util.spec_from_file_location("madhura_seed_demo", REPO_ROOT / "tools" / "seed_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep P2's session-complete sheet out of these tests."""
    monkeypatch.setattr(session_complete, "present_session_complete", lambda *a: None)
    monkeypatch.setattr(session_complete, "show_pending_complete", lambda *_a: None)


@pytest.fixture
def ictx(qapp: QApplication, data_dir: Path, fake_clock: Any, no_summary: None) -> Iterator[AppContext]:
    """A running shell (no tray, reduce-motion, 24 h clock) — window not shown yet."""
    ctx = make_shell(qapp, data_dir, fake_clock)
    ctx.settings.update(time_format="24h")
    yield ctx
    dispose_shell(ctx, qapp)
