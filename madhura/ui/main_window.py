"""MainWindow — sidebar + page stack; implements :class:`~madhura.context.WindowController`.

Responsibilities (spec §5.1, §5.2, §7.4, §8, §1.13):

* four pages in a QStackedWidget (fade on switch, ``on_shown()`` after every switch / show),
  each built on first use (only the Focus page at startup), ``events.navigate`` handling,
  sidebar collapse below 1000 px;
* in-window sheets (FIFO via :class:`SheetHost`) and toasts (:class:`ToastHost`);
* global shortcuts (disabled while a sheet is open; Space is left to the Focus page);
* ``main_visibility_changed`` emission, geometry persistence (kv 'window_geometry'),
  close-to-tray / quit semantics and Windows caption colours.
"""

from __future__ import annotations

import importlib
import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QElapsedTimer, QEvent, QRect, QSize, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication, QHideEvent, QKeyEvent, QKeySequence, QResizeEvent, QShortcut, QShowEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from madhura import APP_NAME
from madhura.platform import windows as win_platform
from madhura.ui import animations, dialogs
from madhura.ui.pages import PAGE_KEYS
from madhura.ui.shortcuts_sheet import ShortcutsSheet
from madhura.ui.sidebar import COLLAPSE_BELOW, Sidebar
from madhura.ui.widgets.empty_state import EmptyState
from madhura.ui.widgets.sheet import SheetContent, SheetHost
from madhura.ui.widgets.toast import ToastHost

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["MainWindow", "GEOMETRY_KEY", "TRAY_HINT_KEY", "DEFAULT_SIZE", "MINIMUM_SIZE", "validate_geometry"]

log = logging.getLogger(__name__)

GEOMETRY_KEY = "window_geometry"
TRAY_HINT_KEY = "tray_hint_shown"
DEFAULT_SIZE = QSize(1120, 760)
MINIMUM_SIZE = QSize(880, 600)
PAGE_FADE_MS = 160
_TITLE_STRIP = 40  # px of the window top that must stay reachable on some screen
_MIN_VISIBLE = 120

_PAGE_CLASSES: dict[str, tuple[str, str]] = {
    "focus": ("madhura.ui.pages.focus_page", "FocusPage"),
    "habits": ("madhura.ui.pages.habits_page", "HabitsPage"),
    "insights": ("madhura.ui.pages.insights_page", "InsightsPage"),
    "settings": ("madhura.ui.pages.settings_page", "SettingsPage"),
}


class _LazyPages(Mapping[str, QWidget]):
    """``MainWindow.pages``: always has every page key; a page is built (and added to the stack)
    on first access, so startup only pays for the Focus page."""

    def __init__(self, create: Callable[[str], QWidget]) -> None:
        self._create = create
        self._built: dict[str, QWidget] = {}
        self._building: set[str] = set()

    def __getitem__(self, key: str) -> QWidget:
        page = self._built.get(key)
        if page is None:
            if key not in PAGE_KEYS or key in self._building:  # unknown, or asked for while being built
                raise KeyError(key)
            self._building.add(key)
            try:
                page = self._built[key] = self._create(key)
            finally:
                self._building.discard(key)
        return page

    def __contains__(self, key: object) -> bool:
        return key in PAGE_KEYS

    def __iter__(self) -> Iterator[str]:
        return iter(PAGE_KEYS)

    def __len__(self) -> int:
        return len(PAGE_KEYS)

    def is_built(self, key: str) -> bool:
        """True once page *key* exists."""
        return key in self._built


def validate_geometry(data: Any, screens: list[QRect]) -> tuple[QRect, bool] | None:
    """Turn a stored ``{x, y, w, h, maximized}`` dict into a usable rect, or None.

    The rect is rejected when its top strip (title bar) does not overlap any of *screens*
    (available geometries) by at least 120×20 px; its size is clamped to the minimum size and to
    the largest screen.
    """
    if not isinstance(data, dict) or not screens:
        return None
    try:
        x, y, w, h = (int(data[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    max_w = max(s.width() for s in screens)
    max_h = max(s.height() for s in screens)
    w = max(MINIMUM_SIZE.width(), min(w, max_w))
    h = max(MINIMUM_SIZE.height(), min(h, max_h))
    strip = QRect(x, y, w, _TITLE_STRIP)
    for screen in screens:
        overlap = strip.intersected(screen)
        if overlap.width() >= _MIN_VISIBLE and overlap.height() >= 20:
            return QRect(x, y, w, h), bool(data.get("maximized", False))
    return None


class MainWindow(QMainWindow):
    """The Madhura main window (``ctx.window``)."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._quitting = False
        self._visible_state: bool | None = None
        self._pending_task_focus = False
        self._caption_applied = False
        self._deactivated = QElapsedTimer()
        self._quit_fn: Callable[[], None] = QApplication.quit
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(MINIMUM_SIZE)
        self.setObjectName("mhMainWindow")

        central = QWidget(self)
        central.setObjectName("mhCentral")
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = Sidebar(ctx, central)
        row.addWidget(self.sidebar)
        self.stack = QStackedWidget(central)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages: Mapping[str, QWidget] = _LazyPages(self._add_page)
        self._current = PAGE_KEYS[0]
        self.stack.setCurrentWidget(self.pages[self._current])

        self.sheet_host = SheetHost(central)
        self.sheet_host.activeChanged.connect(self._on_sheet_active)
        self.toasts = ToastHost(self)

        self._shortcuts: list[QShortcut] = []
        self._install_shortcuts()

        self.sidebar.navigate.connect(self.show_page)
        ctx.events.navigate.connect(self.show_page)
        ctx.events.toast.connect(self._on_event_toast)
        ctx.theme.changed.connect(self._apply_caption_colors)

        self._restore_geometry()

    # ================================================================== pages

    def _add_page(self, key: str) -> QWidget:
        page = self._build_page(key)
        self.stack.addWidget(page)
        return page

    def _build_page(self, key: str) -> QWidget:
        module_name, class_name = _PAGE_CLASSES[key]
        try:
            module = importlib.import_module(module_name)
            page = getattr(module, class_name)(self._ctx)
        except Exception:  # noqa: BLE001 - a broken page must not take the whole window down
            log.exception("Could not create the %s page", key)
            page = EmptyState("alert", "This page couldn't load", "Madhura saved the details to its log.")
        page.setObjectName(f"mhPage_{key}")
        return page

    def page(self, key: str) -> QWidget:
        """The page widget for *key*."""
        return self.pages[key]

    def show_page(self, key: str) -> None:
        """Switch to page *key* (fade), update the sidebar and call the page's ``on_shown()``."""
        if key not in self.pages:
            log.warning("Unknown page %r", key)
            return
        self.sidebar.set_current(key)
        if key == self._current and self.stack.currentWidget() is self.pages[key]:
            return
        old = self.pages.get(self._current)
        self._current = key
        page = self.pages[key]
        fw = QApplication.focusWidget()
        # Focus inside (or on) the outgoing page goes to the new page itself. Parked on the stack
        # meanwhile: the stack would hand it to the new page's first control (which on_shown may
        # hide, e.g. the Insights range control in the empty state), and Qt then passes focus on
        # with a Tab reason — lighting a keyboard focus ring nobody asked for.
        move_focus = fw is None or (old is not None and (fw is old or old.isAncestorOf(fw))) or not fw.isVisible()
        if move_focus:
            self.stack.setFocus(Qt.FocusReason.OtherFocusReason)
        animations.cross_fade(self.stack, self.stack.indexOf(page), PAGE_FADE_MS)
        if move_focus:
            page.setFocus(Qt.FocusReason.OtherFocusReason)
        self._notify_shown(page)

    def _notify_shown(self, page: QWidget) -> None:
        hook = getattr(page, "on_shown", None)
        if callable(hook) and self.isVisible():
            try:
                hook()
            except Exception:  # noqa: BLE001 - refresh errors are logged, never fatal
                log.exception("%s.on_shown failed", type(page).__name__)

    # ================================================================== WindowController

    def show_main(self, page: str | None = None, *, activate: bool = True) -> None:
        """Show the window; ``activate=False`` raises it without taking keyboard focus."""
        if page:
            self.show_page(page)
        if not activate:
            win_platform.bring_to_front(self, steal_focus=False)
            return
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_main(self) -> None:
        """Hide to the tray, or minimize when there is no tray."""
        if self._ctx.tray_available:
            self.hide()
        else:
            self.showMinimized()

    def is_main_visible(self) -> bool:
        """Visible and not minimized."""
        return self.isVisible() and not self.isMinimized()

    def is_main_active(self) -> bool:
        """Visible, not minimized and the active window."""
        return self.is_main_visible() and self.isActiveWindow()

    def was_active_recently(self, ms: int = 400) -> bool:
        """Active now or deactivated within the last *ms* (tray toggle rule)."""
        if self.is_main_active():
            return True
        return self._deactivated.isValid() and self._deactivated.elapsed() <= ms

    def current_page(self) -> str:
        """Key of the page being shown."""
        return self._current

    def focus_task_input(self) -> None:
        """Focus the Focus page's task field; deferred until the window is active.

        Does not switch pages: callers that need the Focus page show it first
        (``show_main("focus")``). Ignored while a sheet is open.
        """
        if self.is_main_active() and not self.has_sheet():
            self._pending_task_focus = False
            self._do_focus_task_input()
        else:
            self._pending_task_focus = True

    def _do_focus_task_input(self) -> None:
        if self._current != "focus":
            return
        hook = getattr(self.pages["focus"], "focus_task_input", None)
        if callable(hook):
            hook()

    def show_sheet(self, content: SheetContent) -> None:
        """Show *content* as an in-window sheet (queued FIFO when one is open)."""
        self.sheet_host.show_sheet(content)

    def close_sheet(self) -> None:
        """Close the current sheet."""
        self.sheet_host.close_sheet()

    def has_sheet(self) -> bool:
        """True while a sheet is open."""
        return self.sheet_host.has_sheet()

    def show_toast(
        self,
        text: str,
        kind: str = "info",
        action_text: str | None = None,
        on_action: Callable[[], object] | None = None,
        duration_ms: int = 4000,
    ) -> int:
        """Show a toast; returns its token (see :class:`ToastHost`)."""
        return self.toasts.show(text, kind, action_text, on_action, duration_ms)

    def _on_event_toast(self, text: str, kind: str) -> None:
        self.show_toast(text, kind)

    def flush_toasts(self) -> None:
        """Expire the visible toast now (commits pending undo-deletes; used on quit)."""
        self.toasts.flush()

    def set_nudge_banner(self, visible: bool) -> None:
        """Forward to ``FocusPage.set_nudge_banner`` when the page has it."""
        hook = getattr(self.pages["focus"], "set_nudge_banner", None)
        if callable(hook):
            hook(bool(visible))

    def quit_app(self) -> None:
        """Quit: confirm when a session would be paused without a tray, pause FOCUS, then quit."""
        engine = self._ctx.engine
        if not self._ctx.tray_available and engine.is_active and not self._quitting:
            ok = dialogs.confirm(
                self if self.isVisible() else None,
                "Quit and pause your session?",
                f"Madhura will pause “{engine.task}” and keep it for when you come back.",
                "Quit",
            )
            if not ok:
                return
        if str(engine.state) == "focus":
            engine.pause()
        self._quitting = True
        self._ctx.flags["quitting"] = True
        self.save_geometry()
        self._quit_fn()

    @property
    def quitting(self) -> bool:
        """True once quit_app() decided to quit."""
        return self._quitting

    # ================================================================== shortcuts

    def _install_shortcuts(self) -> None:
        def add(seq: str | QKeySequence.StandardKey, slot: Callable[[], object], *, page_shortcut: bool = True) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)
            sc.setProperty("mh_page_shortcut", page_shortcut)
            self._shortcuts.append(sc)

        for i, key in enumerate(PAGE_KEYS, start=1):
            add(f"Ctrl+{i}", lambda k=key: self.show_page(k))
        add("Ctrl+N", self._on_new)
        add("Ctrl+Return", self._on_start)
        add("Ctrl+Enter", self._on_start)
        add("Ctrl+Shift+F", self._on_finish)
        add("Ctrl+H", self._on_hide_to_mini)
        add("Ctrl+,", lambda: self.show_page("settings"))
        add("Ctrl+/", self.show_shortcuts)
        add("Ctrl+Q", self.quit_app, page_shortcut=False)

    def shortcuts(self) -> list[QShortcut]:
        """The window's QShortcuts (tests)."""
        return list(self._shortcuts)

    def _on_sheet_active(self, active: bool) -> None:
        for sc in self._shortcuts:
            if sc.property("mh_page_shortcut"):
                sc.setEnabled(not active)
        if active:
            return
        # When the sheet's content hides, Qt hands focus to the next widget in the chain with a
        # *Tab* reason, which would light up a keyboard focus ring on the first sidebar item.
        # Unless the sheet restored focus somewhere meaningful, give it back to the page.
        fw = QApplication.focusWidget()
        if fw is None or self.sidebar.isAncestorOf(fw):
            self.pages[self._current].setFocus(Qt.FocusReason.OtherFocusReason)
        if self._pending_task_focus and self.is_main_active():
            self._pending_task_focus = False
            self._do_focus_task_input()

    def _on_new(self) -> None:
        page = self.pages.get(self._current)
        if self._current == "habits" and callable(getattr(page, "new_habit", None)):
            page.new_habit()  # type: ignore[union-attr]
            return
        self.show_page("focus")
        self.focus_task_input()

    def _on_start(self) -> None:
        if self._current != "focus":
            return
        hook = getattr(self.pages["focus"], "start_from_input", None)
        if callable(hook):
            hook()

    def _on_finish(self) -> None:
        engine = self._ctx.engine
        if engine.is_active:
            engine.finish()

    def _on_hide_to_mini(self) -> None:
        if str(self._ctx.engine.state) != "idle":
            self.hide_main()

    def show_shortcuts(self) -> None:
        """Open the keyboard shortcuts sheet (once)."""
        if isinstance(self.sheet_host.current(), ShortcutsSheet):
            return
        if any(isinstance(s, ShortcutsSheet) for s in self.sheet_host.queued()):
            return
        self.show_sheet(ShortcutsSheet())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self.has_sheet():
            mods = event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier & ~Qt.KeyboardModifier.KeypadModifier
            if event.text() == "?" and not mods:
                self.show_shortcuts()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape and not event.modifiers():
                fw = QApplication.focusWidget()
                if fw is not None and fw.window() is self:
                    fw.clearFocus()
                event.accept()
                return
        super().keyPressEvent(event)

    # ================================================================== events

    def event(self, e: QEvent) -> bool:
        et = e.type()
        if et == QEvent.Type.WindowActivate:
            self._deactivated.invalidate()
            if self._pending_task_focus and not self.has_sheet():
                self._pending_task_focus = False
                self._do_focus_task_input()
        elif et == QEvent.Type.WindowDeactivate:
            self._deactivated.start()
        return super().event(e)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._caption_applied:
            self._caption_applied = True
            self._apply_caption_colors()
        self.sidebar.set_collapsed(self.width() < COLLAPSE_BELOW, animate=False)
        self._emit_visibility(not self.isMinimized())
        self._notify_shown(self.pages[self._current])

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        if not event.spontaneous():
            self.save_geometry()
        self._emit_visibility(False)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            visible = self.isVisible() and not self.isMinimized()
            was = self._visible_state
            self._emit_visibility(visible)
            if visible and was is False:
                self._notify_shown(self.pages[self._current])

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.sidebar.set_collapsed(self.width() < COLLAPSE_BELOW, animate=self.isVisible())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting:
            self.save_geometry()
            event.accept()
            return
        event.ignore()
        if self._ctx.tray_available and self._ctx.settings.get("close_to_tray"):
            self.hide_main()
            self._maybe_show_tray_hint()
            return
        self.quit_app()

    def _maybe_show_tray_hint(self) -> None:
        repo = self._ctx.repo
        try:
            if repo.kv_get(TRAY_HINT_KEY):
                return
            repo.kv_set(TRAY_HINT_KEY, True)
        except sqlite3.Error:
            log.debug("tray hint kv failed", exc_info=True)
            return
        self._ctx.notifier.notify("Madhura is still running in the tray", "Click the tray icon to bring it back.")

    def _emit_visibility(self, visible: bool) -> None:
        if visible == self._visible_state:
            return
        self._visible_state = visible
        self._ctx.events.main_visibility_changed.emit(visible)

    # ================================================================== theme

    def _apply_caption_colors(self) -> None:
        # Windows 11 title bar tint; the platform helpers are no-ops elsewhere and never raise.
        pal = self._ctx.theme.palette
        win_platform.set_dark_title_bar(self, pal.is_dark)
        win_platform.set_caption_colors(self, pal.sidebar, pal.text_muted, pal.border)
        self.update()

    # ================================================================== geometry

    def save_geometry(self) -> None:
        """Persist position/size/maximized to kv 'window_geometry'."""
        if self._ctx.repo.closed:
            return
        maximized = self.isMaximized()
        rect = self.normalGeometry() if (maximized or self.isMinimized()) else self.geometry()
        if not rect.isValid() or rect.width() <= 0:
            return
        data = {"x": rect.x(), "y": rect.y(), "w": rect.width(), "h": rect.height(), "maximized": maximized}
        try:
            self._ctx.repo.kv_set(GEOMETRY_KEY, data)
        except sqlite3.Error:
            log.debug("Could not save window geometry", exc_info=True)

    def _restore_geometry(self) -> None:
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        try:
            stored = self._ctx.repo.kv_get(GEOMETRY_KEY)
        except sqlite3.Error:
            stored = None
        valid = validate_geometry(stored, screens)
        if valid is not None:
            rect, maximized = valid
            self.setGeometry(rect)
            if maximized:
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
            return
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, DEFAULT_SIZE.width(), DEFAULT_SIZE.height())
        w = max(MINIMUM_SIZE.width(), min(DEFAULT_SIZE.width(), avail.width()))
        h = max(MINIMUM_SIZE.height(), min(DEFAULT_SIZE.height(), avail.height()))
        self.resize(w, h)
        self.move(avail.x() + max(0, (avail.width() - w) // 2), avail.y() + max(0, (avail.height() - h) // 2))
