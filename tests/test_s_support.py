"""Shared helpers for the settings / onboarding / system [S] tests (test_s_*.py). No tests here."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget
from test_shell_support import dispose_shell, make_shell

from madhura.context import AppContext
from madhura.core.clock import FakeClock
from madhura.platform import autostart
from madhura.ui.main_window import MainWindow
from madhura.ui.pages.settings_page import SettingsPage

LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_habits.json"


def pump(n: int = 5) -> None:
    """Process pending events a few times."""
    for _ in range(n):
        QApplication.processEvents()


def win_of(ctx: AppContext) -> MainWindow:
    """The real main window."""
    assert isinstance(ctx.window, MainWindow)
    return ctx.window


def settings_page(ctx: AppContext) -> SettingsPage:
    """Show the window on the Settings page and return it."""
    win = win_of(ctx)
    win.resize(1120, 760)
    win.show_main("settings")
    pump()
    page = win.pages["settings"]
    assert isinstance(page, SettingsPage)
    return page


def key(widget: QWidget, k: Qt.Key, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, text: str = "") -> None:
    """Deliver one key press straight to *widget*."""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))


class FakeAutostart:
    """Stands in for platform.autostart (records set_enabled calls)."""

    def __init__(self) -> None:
        self.supported = True
        self.state = "off"
        self.calls: list[bool] = []
        self.succeed = True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeAutostart:
        monkeypatch.setattr(autostart, "is_supported", lambda: self.supported)
        monkeypatch.setattr(autostart, "status", lambda: self.state if self.supported else "off")
        monkeypatch.setattr(autostart, "is_enabled", lambda: self.state in ("on", "needs_repair"))
        monkeypatch.setattr(autostart, "set_enabled", self._set)
        return self

    def _set(self, enabled: bool) -> bool:
        self.calls.append(bool(enabled))
        if self.succeed and self.supported:
            self.state = "on" if enabled else "off"
            return True
        return False


@pytest.fixture(autouse=True)
def _isolated_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never touch the real ~/.config/autostart."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture
def fake_autostart(monkeypatch: pytest.MonkeyPatch) -> FakeAutostart:
    """A controllable autostart backend."""
    return FakeAutostart().install(monkeypatch)


@pytest.fixture
def s_shell(qapp: QApplication, data_dir: Path, fake_clock: FakeClock, fake_autostart: FakeAutostart) -> Iterator[AppContext]:
    """A running shell (no tray) with a fake autostart backend."""
    ctx = make_shell(qapp, data_dir, fake_clock)
    ctx.flags["fake_autostart"] = fake_autostart
    yield ctx
    dispose_shell(ctx, qapp)


@pytest.fixture
def fresh_shell(qapp: QApplication, data_dir: Path, fake_clock: FakeClock, fake_autostart: FakeAutostart) -> Iterator[AppContext]:
    """A not-yet-onboarded shell (no tray) with a fake autostart backend."""
    ctx = make_shell(qapp, data_dir, fake_clock, onboarded=False)
    ctx.flags["fake_autostart"] = fake_autostart
    yield ctx
    dispose_shell(ctx, qapp)


def record(signal: Any) -> list[Any]:
    """Collect a signal's emissions."""
    out: list[Any] = []
    signal.connect(lambda *args: out.append(args[0] if len(args) == 1 else args))
    return out
