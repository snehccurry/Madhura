"""System tray icon + menu (spec §1.12).

The icon is drawn programmatically: an accent ring (idle) or a depleting progress arc while a
session or break runs. It is only regenerated when the arc moves by at least 1/24 turn, the state
changes or the theme changes. The tooltip is refreshed at most every 15 s while ticking.

Its colours follow the taskbar, not the app: on Windows the notification area has its own light /
dark mode (``SystemUsesLightTheme``), so a dark-themed Madhura on the default light Windows 11
taskbar draws the light palette's (darker) accent / break / muted colours, and vice versa.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QElapsedTimer, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from madhura.core.timefmt import fmt_time
from madhura.ui import theme
from madhura.ui.sidebar import paint_ring
from madhura.ui.widgets.menu import Menu

if TYPE_CHECKING:
    from PySide6.QtGui import QAction

    from madhura.context import AppContext

__all__ = ["Tray", "render_tray_pixmap", "tray_icon_for", "tray_tooltip", "tray_palette", "taskbar_is_light", "ICON_SIZES"]

log = logging.getLogger(__name__)

ICON_SIZES: tuple[int, ...] = (16, 20, 24, 32, 48, 64)
ARC_STEPS = 24
TOOLTIP_INTERVAL_MS = 15_000
TOGGLE_MEMORY_MS = 400
_TASK_TOOLTIP_CHARS = 60
TASKBAR_CHECK_MS = 10_000  # re-read the taskbar mode at most this often (while ticking)
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def taskbar_is_light() -> bool | None:
    """Windows: True when the taskbar / notification area is light (``SystemUsesLightTheme`` = 1 —
    not ``AppsUseLightTheme``, which is the app mode), False when dark. None elsewhere, or when
    the value is missing (Windows 10 before 1903: the taskbar is always dark there)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "SystemUsesLightTheme")
    except (ImportError, OSError):
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def tray_palette(palette: theme.Palette | None = None, light_taskbar: bool | None = None) -> theme.Palette:
    """The palette to draw the tray glyph with: the app's, or — when the taskbar's light/dark mode
    is known and differs from the app's — the same accent in the taskbar's scheme."""
    pal = palette or theme.palette()
    if light_taskbar is None or light_taskbar != pal.is_dark:
        return pal
    return theme.build_palette("light" if light_taskbar else "dark", pal.accent_name)


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def render_tray_pixmap(size: int, state: str, fraction: float, palette: theme.Palette | None = None) -> QPixmap:
    """Draw the tray glyph at *size* px: idle → full accent ring + dot (the logo); focus / paused /
    break → faint track + arc of *fraction* (remaining) in accent / muted (+ pause bars) / break
    colour. Stroke ≥ 2 px at 16 px."""
    pal = palette or theme.palette()
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    stroke = max(2.0, size / 8.0)
    inset = 0.5
    rect = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    if state == "idle":
        # The logo: full ring + centre dot.
        color = QColor(pal.accent)
        paint_ring(p, rect, 1.0, color, None, stroke)
        dot = max(2.0, size * 0.16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF((size - 2 * dot) / 2.0, (size - 2 * dot) / 2.0, 2 * dot, 2 * dot))
    else:
        # Running: a faint track and the remaining arc (no dot, so it reads differently from idle).
        if state == "break":
            color = QColor(pal.break_)
        elif state == "paused":
            color = QColor(pal.text_muted)
        else:
            color = QColor(pal.accent)
        track = QColor(color)
        track.setAlphaF(0.22)
        paint_ring(p, rect, fraction, color, track, stroke)
        if state == "paused":
            bar_w = max(1.5, size * 0.11)
            bar_h = size * 0.34
            gap = max(1.5, size * 0.09)
            top = (size - bar_h) / 2.0
            left = (size - 2 * bar_w - gap) / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(left, top, bar_w, bar_h), bar_w / 3, bar_w / 3)
            p.drawRoundedRect(QRectF(left + bar_w + gap, top, bar_w, bar_h), bar_w / 3, bar_w / 3)
    p.end()
    return pm


def tray_icon_for(state: str, fraction: float, palette: theme.Palette | None = None) -> QIcon:
    """Multi-size QIcon (16/20/24/32/48/64 px) for *state* and remaining *fraction*."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(render_tray_pixmap(size, state, fraction, palette))
    return icon


def tray_tooltip(state: str, task: str, remaining_seconds: float) -> str:
    """"Madhura — 23 min left · <task>" / "Madhura — on a break" / "Madhura"."""
    if state == "focus":
        minutes = max(1, int(-(-remaining_seconds // 60)))
        return f"Madhura — {minutes} min left · {_elide(task, _TASK_TOOLTIP_CHARS)}"
    if state == "paused":
        return f"Madhura — paused · {_elide(task, _TASK_TOOLTIP_CHARS)}"
    if state == "break":
        return "Madhura — on a break"
    return "Madhura"


class Tray(QObject):
    """Owns the QSystemTrayIcon (``.tray_icon``) and its menu (``.menu``)."""

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.tray_icon = QSystemTrayIcon(self)
        self.menu = Menu(None)
        self._icon_key: tuple[Any, ...] | None = None
        self._tooltip_clock = QElapsedTimer()
        self._last_state: str | None = None
        self._taskbar_light = taskbar_is_light()
        self._taskbar_clock = QElapsedTimer()
        self._taskbar_clock.start()
        self._actions: dict[str, QAction] = {}
        self._build_menu()
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_activated)
        engine = ctx.engine
        engine.tick.connect(self._on_tick)
        engine.state_changed.connect(self._on_state_changed)
        ctx.theme.changed.connect(self._on_theme)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):  # the OS switched light/dark (taskbar included)
            hints.colorSchemeChanged.connect(self._on_system_scheme)
        self.refresh(force_tooltip=True)

    # ------------------------------------------------------------------ visibility
    def show(self) -> None:
        """Show the tray icon."""
        self.tray_icon.show()

    def hide(self) -> None:
        """Hide the tray icon (on quit)."""
        self.tray_icon.hide()

    def is_visible(self) -> bool:
        """True while the icon is shown."""
        return self.tray_icon.isVisible()

    # ------------------------------------------------------------------ icon + tooltip
    def _state_fraction(self) -> tuple[str, float]:
        e = self._ctx.engine
        state = str(e.state)
        if state in ("focus", "paused"):
            return state, e.remaining_seconds / max(1, e.planned_seconds)
        if state == "break":
            return state, e.break_remaining_seconds / max(1, e.break_total_seconds)
        return "idle", 1.0

    def _on_tick(self) -> None:
        self.refresh(force_tooltip=False)

    def _on_state_changed(self, _state: str) -> None:
        self.refresh(force_tooltip=True)

    def _on_theme(self) -> None:
        self._icon_key = None
        self.refresh(force_tooltip=False)

    def _on_system_scheme(self, *_args: object) -> None:
        self._check_taskbar(force=True)
        self.refresh(force_tooltip=False)

    def _check_taskbar(self, force: bool = False) -> None:
        if force or self._taskbar_clock.elapsed() >= TASKBAR_CHECK_MS:
            self._taskbar_clock.restart()
            self._taskbar_light = taskbar_is_light()

    def refresh(self, force_tooltip: bool = False) -> None:
        """Redraw the icon if the arc moved ≥ 1/24 turn (or state/theme/taskbar mode changed);
        tooltip ≤ every 15 s."""
        state, fraction = self._state_fraction()
        self._check_taskbar()
        pal = tray_palette(theme.palette(), self._taskbar_light)
        bucket = int(round(max(0.0, min(1.0, fraction)) * ARC_STEPS))
        key = (state, bucket, pal.accent, pal.break_, pal.text_muted)
        if key != self._icon_key:
            self._icon_key = key
            self.tray_icon.setIcon(tray_icon_for(state, bucket / ARC_STEPS, pal))
        state_changed = state != self._last_state
        self._last_state = state
        due = not self._tooltip_clock.isValid() or self._tooltip_clock.elapsed() >= TOOLTIP_INTERVAL_MS
        if force_tooltip or state_changed or due:
            self._tooltip_clock.start()
            e = self._ctx.engine
            self.tray_icon.setToolTip(tray_tooltip(state, e.task, e.remaining_seconds))

    def icon_key(self) -> tuple[Any, ...] | None:
        """(state, arc bucket, colours) of the icon currently shown (tests)."""
        return self._icon_key

    # ------------------------------------------------------------------ activation
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()

    def toggle_window(self) -> None:
        """Left click: hide if visible and active within the last 400 ms; else show and activate."""
        window = self._ctx.window
        if window is None:
            return
        recently = getattr(window, "was_active_recently", None)
        active = window.is_main_active() or (callable(recently) and recently(TOGGLE_MEMORY_MS))
        if window.is_main_visible() and active:
            window.hide_main()
        else:
            window.show_main(activate=True)

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        m = self.menu
        a = self._actions
        a["show"] = m.add_action("Show Madhura", self._show_main, icon="expand")
        m.add_separator()
        a["start"] = m.add_action("Start focus…", self._start_focus, icon="play")
        a["pause"] = m.add_action("Pause", self._toggle_pause, icon="pause")
        a["extend"] = m.add_action("+5 min", self._extend, icon="plus")
        a["finish"] = m.add_action("Finish session", self._finish, icon="check")
        m.add_separator()
        a["pill"] = m.add_action("Mini timer: Show", self._toggle_pill, icon="eye_off")
        a["quiet"] = m.add_action("Quiet for 1 hour", self._toggle_quiet, icon="bell_off")
        a["snooze"] = m.add_action("Snooze nudges 30 min", self._snooze_nudges, icon="clock")
        a["wrap"] = m.add_action("Wrap up the day…", self._wrap_up, icon="sunset")
        m.add_separator()
        a["quit"] = m.add_action("Quit Madhura", self._quit, icon="power")
        m.aboutToShow.connect(self.refresh_menu)
        self.refresh_menu()

    def action(self, name: str) -> QAction:
        """Menu action by name: show, start, pause, extend, finish, pill, quiet, snooze, wrap, quit."""
        return self._actions[name]

    def refresh_menu(self) -> None:
        """Sync labels / enabled states with the engine, pill, attention and nudges (aboutToShow)."""
        self._check_taskbar(force=True)
        self.refresh(force_tooltip=False)
        ctx = self._ctx
        e = ctx.engine
        state = str(e.state)
        active = state in ("focus", "paused")
        a = self._actions
        a["start"].setEnabled(state in ("idle", "break"))
        a["pause"].setEnabled(active)
        a["pause"].setText("Resume" if state == "paused" else "Pause")
        a["pause"].setIcon(_icon("play" if state == "paused" else "pause"))
        a["extend"].setEnabled(active)
        a["finish"].setEnabled(active)
        pill = ctx.pill
        showing = bool(pill is not None and pill.is_showing())
        a["pill"].setText("Mini timer: Hide" if showing else "Mini timer: Show")
        a["pill"].setEnabled(pill is not None and state != "idle")
        quiet_until = ctx.attention.quiet_until
        if quiet_until is not None:
            a["quiet"].setText(f"End quiet time ({fmt_time(quiet_until, ctx.use_24h())})")
        else:
            a["quiet"].setText("Quiet for 1 hour")
        nudges = ctx.nudges
        a["snooze"].setEnabled(nudges is not None and ctx.settings.get("nudge_mode") != "off")
        a["wrap"].setEnabled(ctx.window is not None)
        a["show"].setEnabled(ctx.window is not None)

    # ------------------------------------------------------------------ menu actions
    def _show_main(self) -> None:
        if self._ctx.window is not None:
            self._ctx.window.show_main(activate=True)

    def _start_focus(self) -> None:
        window = self._ctx.window
        if window is not None:
            window.show_main("focus", activate=True)
            window.focus_task_input()

    def _toggle_pause(self) -> None:
        self._ctx.engine.toggle_pause()

    def _extend(self) -> None:
        self._ctx.engine.extend(5)

    def _finish(self) -> None:
        self._ctx.engine.finish()

    def _toggle_pill(self) -> None:
        if self._ctx.pill is not None:
            self._ctx.pill.toggle_user_visible()

    def _toggle_quiet(self) -> None:
        attention = self._ctx.attention
        if attention.is_quiet():
            attention.clear_quiet()
        else:
            attention.set_quiet(60)

    def _snooze_nudges(self) -> None:
        if self._ctx.nudges is not None:
            self._ctx.nudges.snooze(30)

    def _wrap_up(self) -> None:
        from madhura.ui.overlays.wrap_up import open_wrap_up

        open_wrap_up(self._ctx)

    def _quit(self) -> None:
        if self._ctx.window is not None:
            self._ctx.window.quit_app()
        else:
            from PySide6.QtWidgets import QApplication

            QApplication.quit()


def _icon(name: str) -> QIcon:
    from madhura.ui import icons

    return icons.icon(name)
