"""Shared fixtures/helpers for the [O] tests (check-ins, nudges, away, pill). Contains no tests.

``octx`` is a full AppContext (create_context + start_services) on the FakeClock whose window is a
:class:`FakeWindowController` (hidden by default, so check-ins go to the popup and the pill shows).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from test_shell_support import release_context

from madhura.context import AppContext, FakeWindowController


def make_octx(qapp: QApplication, data_dir: Path, clock: Any, *, window: Any = None, **settings: Any) -> AppContext:
    """create_context + FakeWindowController + start_services, reduce-motion on."""
    from madhura.app import create_context, start_services
    from madhura.ui.widgets.popup import PopupStack

    PopupStack.reset()
    ctx = create_context(qapp, data_dir, clock, tray_available=False)
    ctx.settings.update(reduce_motion=True, onboarded=True, **settings)
    ctx.window = window if window is not None else FakeWindowController(page="focus", visible=False, active=False)
    start_services(ctx)
    return ctx


def dispose_octx(ctx: AppContext, qapp: QApplication, *, release: bool = True) -> None:
    """Shut the context down and clear global UI state (popup stack, theme, panels).

    ``release=False`` keeps the services' QObjects alive (only their windows go), like an app
    teardown that destroys widgets first; call ``release_context(ctx)`` afterwards.
    """
    from madhura.app import shutdown
    from madhura.ui import theme
    from madhura.ui.widgets.popup import PopupStack

    shutdown(ctx)
    for svc in (ctx.checkins, ctx.nudges, ctx.away):
        for attr in ("popup", "panel"):
            panel = getattr(svc, attr, None) if svc is not None else None
            if panel is not None:
                try:
                    panel.hide()
                    panel.deleteLater()
                except RuntimeError:
                    pass
    if ctx.pill is not None:
        ctx.pill.pill.hide()
        ctx.pill.pill.deleteLater()
    PopupStack.reset()
    theme.set_current(None)
    qapp.setStyleSheet("")
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    if release:
        release_context(ctx)


@pytest.fixture
def octx(qapp: QApplication, data_dir: Path, fake_clock: Any) -> Iterator[AppContext]:
    """Running services with a hidden FakeWindowController on the fake clock."""
    ctx = make_octx(qapp, data_dir, fake_clock)
    yield ctx
    dispose_octx(ctx, qapp)


def advance(ctx: AppContext, seconds: float, step: float = 10.0) -> None:
    """Advance the FakeClock in small steps, polling the engine (ticks drive the services)."""
    remaining = float(seconds)
    while remaining > 1e-9:
        d = min(step, remaining)
        ctx.clock.advance(d)  # type: ignore[attr-defined]
        ctx.engine.poll()
        remaining -= d


def set_idle(monkeypatch: pytest.MonkeyPatch, ctx: AppContext, value: float | None) -> None:
    """Make ``attention.input_idle_seconds()`` return *value*."""
    monkeypatch.setattr(ctx.attention, "input_idle_seconds", lambda: value)


def set_attention(monkeypatch: pytest.MonkeyPatch, ctx: AppContext, state: str, emit: bool = True) -> None:
    """Force ``attention.state()`` and (optionally) emit ``changed``."""
    monkeypatch.setattr(ctx.attention, "state", lambda: state)
    if emit:
        ctx.attention.changed.emit(state)


def records(ctx: AppContext, sid: int | None = None) -> list[tuple[str, str | None]]:
    """(response, reason) of the session's check-ins, oldest first."""
    sid = sid if sid is not None else ctx.engine.session_id
    assert sid is not None
    return [(str(c.response), c.reason) for c in ctx.repo.checkins_for_session(sid)]
