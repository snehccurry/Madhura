"""Application context, app-wide event bus and the window-controller protocol (spec §5.1).

Everything a feature needs is reachable from one :class:`AppContext` (``ctx``): the clock, the
repository, settings, theme, engine, sound, notifier, attention monitor, the main window (through
the :class:`WindowController` protocol) and the feature services (filled in by
``app.start_services``). Tests can use :class:`FakeWindowController` instead of a real window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from PySide6.QtCore import QObject, Signal

from madhura.core.days import day_bounds as _day_bounds
from madhura.core.days import logical_date

if TYPE_CHECKING:
    from madhura.core.clock import Clock
    from madhura.data.repository import Repository
    from madhura.data.settings import SettingsStore
    from madhura.services.attention import AttentionMonitor
    from madhura.services.away_service import AwayService
    from madhura.services.checkin_service import CheckInService
    from madhura.services.engine import FocusEngine
    from madhura.services.habit_reminders import HabitReminderService
    from madhura.services.notifier import Notifier
    from madhura.services.nudge_service import NudgeService
    from madhura.services.sound import SoundPlayer
    from madhura.ui.overlays.pill import PillController
    from madhura.ui.theme import ThemeManager
    from madhura.ui.widgets.sheet import SheetContent

__all__ = [
    "PAGE_KEYS",
    "DATA_KINDS",
    "AppEvents",
    "WindowController",
    "AppContext",
    "FakeWindowController",
    "system_uses_24h",
]

log = logging.getLogger(__name__)

#: Main-window pages, in sidebar order (Ctrl+1..4).
PAGE_KEYS: tuple[str, ...] = ("focus", "habits", "insights", "settings")

#: Values carried by :attr:`AppEvents.data_changed`.
DATA_KINDS: tuple[str, ...] = ("sessions", "checkins", "tasks", "habits", "captures", "settings", "all")


class AppEvents(QObject):
    """App-wide signal bus. Receivers mark themselves dirty and refresh when visible."""

    #: "sessions" | "checkins" | "tasks" | "habits" | "captures" | "settings" | "all"
    data_changed = Signal(str)
    #: Page key ("focus", "habits", "insights", "settings") the main window should switch to.
    navigate = Signal(str)
    #: True = main window visible and not minimized.
    main_visibility_changed = Signal(bool)
    #: The logical day rolled over (60 s app timer) or may have (after away_detected).
    day_changed = Signal()
    #: Simple toast: (text, kind). Use ``ctx.window.show_toast`` for toasts with an action.
    toast = Signal(str, str)


@runtime_checkable
class WindowController(Protocol):
    """What features may ask of the main window (implemented by ``ui.main_window.MainWindow``)."""

    def show_main(self, page: str | None = None, *, activate: bool = True) -> None:
        """Show the window (optionally switching page). ``activate=False`` never steals focus."""
        ...

    def hide_main(self) -> None:
        """Hide to the tray, or minimize when there is no tray."""
        ...

    def is_main_visible(self) -> bool:
        """Visible and not minimized."""
        ...

    def is_main_active(self) -> bool:
        """Visible, not minimized and the active window."""
        ...

    def current_page(self) -> str:
        """Key of the page being shown."""
        ...

    def focus_task_input(self) -> None:
        """Focus the Focus page's task field (deferred until the window is active)."""
        ...

    def show_sheet(self, content: SheetContent) -> None:
        """Show an in-window sheet (FIFO queue if one is already open)."""
        ...

    def close_sheet(self) -> None:
        """Close the current sheet."""
        ...

    def has_sheet(self) -> bool:
        """True while a sheet is open."""
        ...

    def show_toast(
        self,
        text: str,
        kind: str = "info",
        action_text: str | None = None,
        on_action: Callable[[], object] | None = None,
        duration_ms: int = 4000,
    ) -> Any:
        """Show a toast at the bottom of the window."""
        ...

    def set_nudge_banner(self, visible: bool) -> None:
        """Show/hide the firm-nudge banner on the Focus page."""
        ...

    def quit_app(self) -> None:
        """Quit Madhura (pausing a running session)."""
        ...


_SYSTEM_24H: list[bool] = []


def system_uses_24h() -> bool:
    """True when the OS locale's short time format is 24-hour (resolved once, then cached)."""
    if not _SYSTEM_24H:
        try:
            from PySide6.QtCore import QLocale

            fmt = QLocale.system().timeFormat(QLocale.FormatType.ShortFormat)
            _SYSTEM_24H.append("a" not in fmt.lower())
        except Exception:  # noqa: BLE001 - locale probing must never break the app
            log.debug("Could not read the system time format", exc_info=True)
            _SYSTEM_24H.append(True)
    return _SYSTEM_24H[0]


@dataclass(eq=False)
class AppContext:
    """Everything the UI and services share. Built by ``app.create_context``."""

    app: Any
    clock: Clock
    repo: Repository
    settings: SettingsStore
    theme: ThemeManager
    engine: FocusEngine
    sound: SoundPlayer
    notifier: Notifier
    events: AppEvents
    attention: AttentionMonitor
    data_dir: Path
    tray_available: bool
    window: WindowController | None = None
    checkins: CheckInService | None = None
    nudges: NudgeService | None = None
    habit_reminders: HabitReminderService | None = None
    pill: PillController | None = None
    away: AwayService | None = None
    #: The system tray wrapper (``ui.tray.Tray``) when one was created (extension).
    tray: Any = None
    #: The 60 s logical-day watcher created by ``app.start_services`` (extension).
    day_watcher: Any = None
    #: Free-form per-run state for the shell (e.g. ``"quitting"``); not persisted.
    flags: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ time helpers

    def day_start_hour(self) -> int:
        """``settings.day_start_hour`` (0–6)."""
        return int(self.settings.get("day_start_hour"))

    def today(self) -> date:
        """Logical date of ``clock.now()`` with ``settings.day_start_hour``."""
        return logical_date(self.clock.now(), self.day_start_hour())

    def day_bounds(self, d: date | None = None) -> tuple[datetime, datetime]:
        """Naive local ``[start, end)`` of logical date *d* (default: today)."""
        return _day_bounds(d if d is not None else self.today(), self.day_start_hour())

    def use_24h(self) -> bool:
        """``settings.time_format`` resolved ("system" asks the OS locale once)."""
        fmt = self.settings.get("time_format")
        if fmt == "24h":
            return True
        if fmt == "12h":
            return False
        return system_uses_24h()


class FakeWindowController:
    """Records :class:`WindowController` calls for tests; keeps a simple visible/active/page model.

    ``calls`` holds ``(method, args, kwargs)`` tuples; ``sheets`` the shown (still open) sheets in
    FIFO order; ``toasts`` every ``(text, kind, action_text)`` shown.
    """

    def __init__(self, page: str = "focus", *, visible: bool = True, active: bool = True) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.page = page
        self.visible = visible
        self.active = active
        self.sheets: list[Any] = []
        self.toasts: list[tuple[str, str, str | None]] = []
        self.toast_actions: list[Callable[[], object] | None] = []
        self.nudge_banner = False
        self.task_focus_requests = 0
        self.quit_requested = False

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def called(self, name: str) -> int:
        """How many times *name* was called."""
        return sum(1 for c in self.calls if c[0] == name)

    # ------------------------------------------------------------------ protocol

    def show_main(self, page: str | None = None, *, activate: bool = True) -> None:
        self._record("show_main", page, activate=activate)
        if page:
            self.page = page
        self.visible = True
        if activate:
            self.active = True

    def hide_main(self) -> None:
        self._record("hide_main")
        self.visible = False
        self.active = False

    def is_main_visible(self) -> bool:
        return self.visible

    def is_main_active(self) -> bool:
        return self.visible and self.active

    def current_page(self) -> str:
        return self.page

    def focus_task_input(self) -> None:
        self._record("focus_task_input")
        self.task_focus_requests += 1

    def show_sheet(self, content: Any) -> None:
        self._record("show_sheet", content)
        self.sheets.append(content)

    def close_sheet(self) -> None:
        self._record("close_sheet")
        if self.sheets:
            content = self.sheets.pop(0)
            finished = getattr(content, "finished", None)
            if finished is not None:
                finished.emit()

    def has_sheet(self) -> bool:
        return bool(self.sheets)

    def show_toast(
        self,
        text: str,
        kind: str = "info",
        action_text: str | None = None,
        on_action: Callable[[], object] | None = None,
        duration_ms: int = 4000,
    ) -> int:
        self._record("show_toast", text, kind=kind, action_text=action_text, duration_ms=duration_ms)
        self.toasts.append((text, kind, action_text))
        self.toast_actions.append(on_action)
        return len(self.toasts)

    def set_nudge_banner(self, visible: bool) -> None:
        self._record("set_nudge_banner", visible)
        self.nudge_banner = bool(visible)

    def quit_app(self) -> None:
        self._record("quit_app")
        self.quit_requested = True
