"""Shared helpers for the app-shell tests (test_shell_*.py). Contains no tests itself."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication

from madhura.context import AppContext


def make_shell(
    qapp: QApplication,
    data_dir: Path,
    clock: Any,
    *,
    tray: bool = False,
    services: bool = True,
    onboarded: bool = True,
) -> AppContext:
    """create_context + create_main_window (+ start_services) with instant animations.

    The window's quit hook is replaced with a recorder so ``quit_app()`` never quits the test app.
    """
    from madhura.app import create_context, create_main_window, start_services
    from madhura.ui.widgets.popup import PopupStack

    PopupStack.reset()
    ctx = create_context(qapp, data_dir, clock, tray_available=tray)
    ctx.settings.update(reduce_motion=True, onboarded=onboarded)
    window = create_main_window(ctx)
    ctx.flags["quit_calls"] = 0

    def fake_quit() -> None:
        ctx.flags["quit_calls"] += 1

    window._quit_fn = fake_quit  # noqa: SLF001
    if services:
        start_services(ctx)
    return ctx


def release_context(ctx: AppContext) -> None:
    """Delete a shut-down context's QObjects (services, engine, settings, events, …).

    Lambdas connected to their signals are referenced from the C++ side, where the garbage
    collector cannot see them, so without this every context (and its window) stays alive for
    the rest of the run.
    """
    from shiboken6 import isValid

    from madhura.ui import theme

    if theme._current is ctx.theme:  # noqa: SLF001
        theme.set_current(None)
    pill_window = getattr(ctx.pill, "pill", None)  # parentless, owned by the controller
    if isinstance(pill_window, QObject) and isValid(pill_window):
        pill_window.hide()
        pill_window.deleteLater()
    names = ("pill", "checkins", "nudges", "away", "habit_reminders", "day_watcher", "tray",
             "engine", "attention", "sound", "notifier", "settings", "theme", "events")
    for name in names:
        obj = getattr(ctx, name, None)
        if isinstance(obj, QObject) and isValid(obj):
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def dispose_shell(ctx: AppContext, qapp: QApplication) -> None:
    """Shut the context down and destroy its window / global UI state."""
    from madhura.app import shutdown
    from madhura.ui import theme
    from madhura.ui.widgets.popup import PopupStack

    shutdown(ctx)
    window = ctx.window
    if window is not None:
        window._quitting = True  # noqa: SLF001 - let close() through
        window.hide()
        window.deleteLater()  # type: ignore[attr-defined]
    if ctx.tray is not None:
        ctx.tray.hide()
    PopupStack.reset()
    qapp.processEvents()
    # processEvents() does not run deleteLater(); without this, dead windows pile up and every
    # later app.setStyleSheet() re-polishes them (so delete before clearing the QSS below).
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    release_context(ctx)
    theme.set_current(None)
    qapp.setStyleSheet("")
    qapp.processEvents()


@pytest.fixture
def shell(qapp: QApplication, data_dir: Path, fake_clock: Any) -> Iterator[AppContext]:
    """A running shell (no tray) on the per-test data dir and the fake clock."""
    ctx = make_shell(qapp, data_dir, fake_clock)
    yield ctx
    dispose_shell(ctx, qapp)


@pytest.fixture
def tray_shell(qapp: QApplication, data_dir: Path, fake_clock: Any) -> Iterator[AppContext]:
    """Like :func:`shell` but with a (forced) system tray."""
    ctx = make_shell(qapp, data_dir, fake_clock, tray=True)
    yield ctx
    dispose_shell(ctx, qapp)


def advance(ctx: AppContext, seconds: float, step: float = 60.0) -> None:
    """Advance the FakeClock in ≤ 90 s steps, polling the engine (bigger jumps look like sleep)."""
    remaining = float(seconds)
    while remaining > 0:
        d = min(step, remaining)
        ctx.clock.advance(d)  # type: ignore[attr-defined]
        ctx.engine.poll()
        remaining -= d
