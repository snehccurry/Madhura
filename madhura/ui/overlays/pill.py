"""Mini timer pill: :class:`FocusPill` (a FloatingPanel) + :class:`PillController` (spec §1.6).

The pill is a parentless, never-activating, always-on-top capsule (36 px tall, radius 18):
``10 px pad · 18 px ring · 8 px · time 13/600 tabular · 12 px pad``. It shows whole minutes
("23m", repainted once a minute) unless ``pill_show_seconds``; the final minute shows M:SS.
Hovering for 150 ms expands it (x and width animate together, 180 ms OutCubic) to
``MM:SS · task (elided at 220 px) · [pause/resume] [open]``; it collapses 350 ms after the pointer
leaves. A pill right of the screen centre keeps its right edge and grows left; otherwise it grows
right (max 320 px).

Dragging starts after ``QApplication.startDragDistance()``; a release within 16 px of an
available-geometry edge snaps to a 12 px inset (160 ms). The position is persisted in kv
'pill_pos' = {screen, dx_right, dy_bottom} relative to that screen's available geometry; a missing
screen or an off-screen rect falls back to the bottom-right corner of the primary screen (inset 16).

States: focus (accent ring), paused (pause glyph, muted time; "Paused · were you away?" after two
missed check-ins), break (coffee glyph, break colour), done (success check + "Done" for 3 s, then
hidden). Window opacity 0.94, 1.0 on hover. At 120 s left the border pulses once per session
(skipped with reduce-motion).

Visibility (PillController): state ≠ IDLE and (pill_mode "always" or (pill_mode "hidden" and the
main window is not visible)) and not hidden by the user (tray toggle; resets when the session
ends) and attention ≠ quiet — driven by signals, never polled.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QEnterEvent,
    QFontMetrics,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QScreen,
)
from PySide6.QtWidgets import QApplication

try:  # shiboken6 ships with PySide6
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover
    def _is_valid(_obj: object) -> bool:
        return True

from madhura.core.timefmt import fmt_clock, fmt_minutes_short
from madhura.ui import animations, fonts, icons, theme
from madhura.ui.overlays._panel_kit import install_focus_guard
from madhura.ui.sidebar import paint_ring
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import IconButton
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.popup import FloatingPanel, PopupStack, paint_shadow

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "FocusPill",
    "PillController",
    "POS_KEY",
    "snap_rect",
    "position_to_kv",
    "position_from_kv",
    "default_card_rect",
    "pill_time_text",
]

log = logging.getLogger(__name__)

POS_KEY = "pill_pos"
HEIGHT = 36
RADIUS = 18.0
SHADOW = 12
MAX_WIDTH = 320
TASK_MAX = 220
LEFT_PAD = 10
RING = 18
GAP = 8
RIGHT_PAD = 12
BUTTON = 28
BUTTON_GAP = 2
BUTTON_RIGHT = 6
RING_THICKNESS = 2.5
EXPAND_DELAY_MS = 150
COLLAPSE_GRACE_MS = 350
EXPAND_MS = 180
SNAP_DISTANCE = 16
SNAP_INSET = 12
SNAP_MS = 160
DEFAULT_INSET = 16
DONE_MS = 3000
PULSE_SECONDS = 120
PULSE_MS = 900
REST_OPACITY = 0.94
FADE_IN_MS = 180
FADE_OUT_MS = 140
MISSED_TEXT = "Paused · were you away?"


# =========================================================================== pure helpers


def pill_time_text(remaining: float, show_seconds: bool) -> str:
    """Collapsed time label: "23m" (whole minutes, rounded up), "M:SS" in the final minute, or
    ``fmt_clock`` ("MM:SS" / "H:MM:SS") when seconds are shown."""
    if show_seconds:
        return fmt_clock(remaining)
    total = max(0, int(math.ceil(float(remaining) - 1e-6)))
    if total <= 60:
        return f"{total // 60}:{total % 60:02d}"
    return fmt_minutes_short(total)


def snap_rect(rect: QRect, avail: QRect, distance: int = SNAP_DISTANCE, inset: int = SNAP_INSET) -> QRect:
    """Keep *rect* inside *avail*; an edge within *distance* px of the screen edge snaps to *inset*."""
    r = QRect(rect)
    ax0, ay0 = avail.x(), avail.y()
    ax1, ay1 = avail.x() + avail.width(), avail.y() + avail.height()
    x = min(max(r.x(), ax0), ax1 - r.width())
    y = min(max(r.y(), ay0), ay1 - r.height())
    if x - ax0 <= distance:
        x = ax0 + inset
    elif ax1 - (x + r.width()) <= distance:
        x = ax1 - inset - r.width()
    if y - ay0 <= distance:
        y = ay0 + inset
    elif ay1 - (y + r.height()) <= distance:
        y = ay1 - inset - r.height()
    r.moveTo(x, y)
    return r


def default_card_rect(width: int, avail: QRect, inset: int = DEFAULT_INSET) -> QRect:
    """Bottom-right corner of *avail*, *inset* px from both edges."""
    return QRect(avail.x() + avail.width() - inset - width, avail.y() + avail.height() - inset - HEIGHT, width, HEIGHT)


def position_to_kv(card: QRect, screen_name: str, avail: QRect) -> dict[str, Any]:
    """{'screen', 'dx_right', 'dy_bottom'} of *card* relative to *avail* (its screen)."""
    return {
        "screen": screen_name,
        "dx_right": int(avail.x() + avail.width() - (card.x() + card.width())),
        "dy_bottom": int(avail.y() + avail.height() - (card.y() + card.height())),
    }


def position_from_kv(data: Any, width: int, screens: list[tuple[str, QRect]], primary: QRect) -> QRect:
    """Card rect from a stored position; default bottom-right of *primary* when the screen is
    missing, the data is malformed or the rect would be off every screen."""
    fallback = default_card_rect(width, primary)
    if not isinstance(data, dict):
        return fallback
    name = data.get("screen")
    dx, dy = data.get("dx_right"), data.get("dy_bottom")
    if not isinstance(dx, int) or not isinstance(dy, int) or isinstance(dx, bool) or isinstance(dy, bool):
        return fallback
    avail = next((geo for n, geo in screens if n == name), None)
    if avail is None:
        return fallback
    rect = QRect(avail.x() + avail.width() - dx - width, avail.y() + avail.height() - dy - HEIGHT, width, HEIGHT)
    if not any(geo.contains(rect.center()) for _n, geo in screens):
        return fallback
    return snap_rect(rect, avail, distance=-1, inset=0)


# =========================================================================== the pill


class FocusPill(FloatingPanel):
    """Compact always-on-top timer capsule (see the module docstring).

    Signals: ``open_requested`` (double-click / open button), ``toggle_requested``
    (pause/resume/end-break button), ``menu_requested(QPoint)`` (right-click, global position),
    ``position_changed`` (a drag ended; the controller persists it).
    """

    open_requested = Signal()
    toggle_requested = Signal()
    menu_requested = Signal(QPoint)
    position_changed = Signal()

    def __init__(self, ctx: AppContext | None = None) -> None:
        super().__init__("pill", width=120, padding=0, radius=RADIUS, margin=SHADOW)
        install_focus_guard()
        self._ctx = ctx
        self.setAccessibleName("Mini timer")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._time_font = fonts.font(13, 600, tabular=True)
        self._task_font = fonts.font(13, 500)

        self._mode = "focus"  # focus | paused | break | done
        self._time_collapsed = ""
        self._time_expanded = ""
        self._label = ""  # replaces the collapsed time ("Paused · were you away?")
        self._task = ""
        self._fraction = 1.0
        self._paint_key: tuple[Any, ...] | None = None
        self._buttons_mode = ""

        self._expanded = False
        self._grow_left = True
        self._card = QRect()  # collapsed card, global coordinates (canonical position)
        self._hovered = False
        self._press_global: QPoint | None = None
        self._press_offset = QPoint()
        self._dragging = False
        self._suppress_expand = False
        self._pulse = 0.0
        self._pulse_anim: QVariantAnimation | None = None
        self._fade_anim: QPropertyAnimation | None = None
        self._hiding = False

        self.toggle_button = IconButton("pause", "Pause", BUTTON, self, glyph=16)
        self.open_button = IconButton("expand", "Open Madhura", BUTTON, self, glyph=16)
        for b in (self.toggle_button, self.open_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.hide()
        self.toggle_button.clicked.connect(self.toggle_requested)
        self.open_button.clicked.connect(self.open_requested)

        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.setInterval(EXPAND_DELAY_MS)
        self._expand_timer.timeout.connect(self._on_expand_timer)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(COLLAPSE_GRACE_MS)
        self._collapse_timer.timeout.connect(lambda: self.set_expanded(False))
        self.set_card_width(self.collapsed_width(), resize=False)
        self.resize(self.collapsed_width() + 2 * SHADOW, HEIGHT + 2 * SHADOW)
        self.setWindowOpacity(REST_OPACITY)

    # ================================================================== FloatingPanel overrides
    def desired_height(self) -> int:
        """Window height (fixed: 36 px card + shadow)."""
        return HEIGHT + 2 * SHADOW

    def card_size(self) -> QSize:
        """Current card size."""
        return QSize(self.width() - 2 * SHADOW, HEIGHT)

    def sync_size(self, animate: bool = True, duration: int = 180) -> None:
        """The pill sizes itself; nothing to fit."""

    def _on_layout_request(self) -> None:
        """The pill has no managed content; ignore layout requests."""

    def present(self, priority: int | None = None) -> None:
        """The pill is not stacked: show it in place."""
        self.show_pill()

    def dismiss(self, animate: bool = True) -> None:
        """Hide the pill (fade unless *animate* is False)."""
        self.hide_pill(animate=animate)

    # ================================================================== content
    def mode(self) -> str:
        """"focus" | "paused" | "break" | "done"."""
        return self._mode

    def time_text(self) -> str:
        """Text shown next to the glyph right now (collapsed or expanded form)."""
        if self._expanded:
            return self._time_expanded
        return self._label or self._time_collapsed

    def task_text(self) -> str:
        """Task (or break label) shown when expanded."""
        return self._task

    def fraction(self) -> float:
        """Ring fraction (remaining)."""
        return self._fraction

    def set_display(
        self,
        mode: str,
        *,
        collapsed: str,
        expanded: str,
        task: str = "",
        fraction: float = 1.0,
        label: str = "",
    ) -> None:
        """Update what the pill shows; repaints / resizes only when something visible changed."""
        self._mode = mode
        self._time_collapsed = collapsed
        self._time_expanded = expanded
        self._task = task
        self._label = label
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self._update_buttons_for_mode()
        key = (
            mode,
            self.time_text(),
            task if self._expanded else "",
            label,
            round(self._fraction * 120) if not self._expanded else round(self._fraction * 600),
        )
        width_changed = self._apply_width()
        if key != self._paint_key or width_changed:
            self._paint_key = key
            self.update()
        self._update_accessibility()

    def _update_buttons_for_mode(self) -> None:
        mode = self._mode
        if mode == self._buttons_mode:
            return
        self._buttons_mode = mode
        if mode == "paused":
            self.toggle_button.set_icon_name("play")
            self.toggle_button.set_tooltip("Resume")
        elif mode == "break":
            self.toggle_button.set_icon_name("skip")
            self.toggle_button.set_tooltip("End break")
        else:
            self.toggle_button.set_icon_name("pause")
            self.toggle_button.set_tooltip("Pause")
        self.toggle_button.setEnabled(mode != "done")

    def _update_accessibility(self) -> None:
        """Tooltip + accessible description, in whole minutes (changes once a minute)."""
        left = self._time_collapsed
        if self._mode == "done":
            desc = "Session done"
        elif self._mode == "break":
            desc = f"{self._task or 'Break'} · {left} left"
        elif self._mode == "paused":
            desc = self._label or f"Paused · {left} left on {self._task}"
        else:
            desc = f"{left} left on {self._task}"
        if desc != self.accessibleDescription():
            self.setAccessibleDescription(desc)
        tip = "" if self._expanded else desc
        if tip != self.toolTip():
            self.setToolTip(tip)

    # ================================================================== geometry
    def _text_w(self, text: str, font: Any = None) -> int:
        return QFontMetrics(font or self._time_font).horizontalAdvance(text)

    def collapsed_width(self) -> int:
        """Card width when collapsed (depends on the current label)."""
        text = self._label or self._time_collapsed or "00m"
        return LEFT_PAD + RING + GAP + self._text_w(text) + RIGHT_PAD

    def expanded_width(self) -> int:
        """Card width when expanded (≤ 320)."""
        base = LEFT_PAD + RING + GAP + self._text_w(self._time_expanded or "00:00") + GAP
        buttons = BUTTON + BUTTON_GAP + BUTTON + BUTTON_RIGHT
        # +3 px slack: fractional glyph advances at 125–175 % scaling must not elide a text that fits.
        task_w = min(TASK_MAX, QFontMetrics(self._task_font).horizontalAdvance(self._task) + 3) if self._task else 0
        if task_w:
            task_w = min(task_w, MAX_WIDTH - base - buttons - GAP)
            return min(MAX_WIDTH, base + task_w + GAP + buttons)
        return min(MAX_WIDTH, base + buttons - GAP + 4)

    def current_card_width(self) -> int:
        """Width the card should have in the current (collapsed / expanded) state."""
        return self.expanded_width() if self._expanded else self.collapsed_width()

    def collapsed_card(self) -> QRect:
        """The collapsed card rectangle in global coordinates (the persisted position)."""
        return QRect(self._card)

    def set_collapsed_card(self, rect: QRect, animate: bool = False, duration: int = SNAP_MS) -> None:
        """Place the collapsed card at *rect* (global); the window follows (expanded or not)."""
        self._card = QRect(rect.x(), rect.y(), self.collapsed_width(), HEIGHT)
        self._apply_geometry(animate=animate, duration=duration)

    def _screen_avail(self, rect: QRect | None = None) -> QRect | None:
        r = rect if rect is not None else self._card
        screen = QGuiApplication.screenAt(r.center()) if not r.isNull() else None
        screen = screen or self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def grows_left(self) -> bool:
        """True when the pill's centre is right of its screen's centre (right edge stays put)."""
        avail = self._screen_avail()
        if avail is None or self._card.isNull():
            return True
        return self._card.center().x() > avail.center().x()

    def _apply_width(self) -> bool:
        """Re-fit the collapsed width after a label change (keeps the outer edge fixed)."""
        new_w = self.collapsed_width()
        if self._card.isNull() or new_w == self._card.width():
            expanded_off = self._expanded and self.width() - 2 * SHADOW != self.expanded_width()
            if not self._card.isNull() and expanded_off and not self.is_animating() and not self._dragging:
                self._apply_geometry(animate=False)
                return True
            return False
        if self.grows_left():
            self._card = QRect(self._card.x() + self._card.width() - new_w, self._card.y(), new_w, HEIGHT)
        else:
            self._card = QRect(self._card.x(), self._card.y(), new_w, HEIGHT)
        self.set_card_width(new_w, resize=False)
        self._apply_geometry(animate=False)
        return True

    def target_card_rect(self) -> QRect:
        """Global card rect for the current expansion state."""
        card = QRect(self._card)
        if not self._expanded:
            return card
        w = self.expanded_width()
        if self._grow_left:
            return QRect(card.x() + card.width() - w, card.y(), w, HEIGHT)
        return QRect(card.x(), card.y(), w, HEIGHT)

    def _apply_geometry(self, animate: bool, duration: int = EXPAND_MS) -> None:
        if self._card.isNull():
            return
        target = self.window_rect_for_card(self.target_card_rect())
        self.move_to(target, animate=animate and self.isVisible() and animations.motion_enabled(), duration=duration)

    # ================================================================== expansion
    def is_expanded(self) -> bool:
        """True while expanded (or expanding)."""
        return self._expanded

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        """Expand / collapse (x and width animate together, 180 ms OutCubic)."""
        expanded = bool(expanded) and self._mode != "done"
        if expanded == self._expanded:
            return
        if expanded:
            self._grow_left = self.grows_left()
        self._expanded = expanded
        self._paint_key = None
        self._apply_geometry(animate=animate)
        self._layout_buttons()
        self._update_accessibility()
        self.update()

    def _on_expand_timer(self) -> None:
        if self._dragging or self._suppress_expand or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        if self._hovered:
            self.set_expanded(True)

    def _layout_buttons(self) -> None:
        m = SHADOW
        card_w = self.width() - 2 * m
        right = m + card_w - BUTTON_RIGHT
        y = m + (HEIGHT - BUTTON) // 2
        self.open_button.setGeometry(right - BUTTON, y, BUTTON, BUTTON)
        self.toggle_button.setGeometry(right - 2 * BUTTON - BUTTON_GAP, y, BUTTON, BUTTON)
        time_w = self._text_w(self._time_expanded or "00:00")
        needed = LEFT_PAD + RING + GAP + time_w + GAP + 2 * BUTTON + BUTTON_GAP + BUTTON_RIGHT
        show = self._expanded and card_w >= needed - 2
        self.toggle_button.setVisible(show)
        self.open_button.setVisible(show)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_buttons()

    # ================================================================== show / hide
    def show_pill(self) -> None:
        """Fade in at the current position (0 → 0.94)."""
        self._hiding = False
        self._stop_fade()
        self._apply_geometry(animate=False)
        if self.isVisible():
            self.setWindowOpacity(1.0 if self._hovered else REST_OPACITY)
            return
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_to(REST_OPACITY, FADE_IN_MS if animations.motion_enabled() else 100)

    def hide_pill(self, animate: bool = True) -> None:
        """Fade out and hide (collapsing it)."""
        if not self.isVisible():
            self._reset_interaction()
            return
        if not animate:
            self._stop_fade()
            self.hide()
            self._reset_interaction()
            return
        self._hiding = True
        self._fade_to(0.0, FADE_OUT_MS if animations.motion_enabled() else 100, on_done=self._finish_hide)

    def _finish_hide(self) -> None:
        if self._hiding:
            self._hiding = False
            self.hide()
            self.setWindowOpacity(REST_OPACITY)
            self._reset_interaction()

    def is_hiding(self) -> bool:
        """True while the fade-out runs."""
        return self._hiding

    def _reset_interaction(self) -> None:
        self._expand_timer.stop()
        self._collapse_timer.stop()
        self._hovered = False
        self._dragging = False
        self._press_global = None
        if self._expanded:
            self._expanded = False
            self._apply_geometry(animate=False)
            self._layout_buttons()

    def _stop_fade(self) -> None:
        anim = self._fade_anim
        self._fade_anim = None
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass

    def _fade_to(self, end: float, ms: int, on_done: Any = None) -> None:
        self._stop_fade()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(end)

        def done() -> None:
            if self._fade_anim is anim:
                self._fade_anim = None
                if on_done is not None:
                    on_done()

        anim.finished.connect(done)
        self._fade_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    # ================================================================== pulse
    def pulse(self) -> bool:
        """Pulse the border once (accent glow, 900 ms). Skipped with reduce-motion. Returns True
        when an animation started."""
        if not animations.motion_enabled() or not self.isVisible():
            return False
        if self._pulse_anim is not None:
            self._pulse_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(PULSE_MS)
        anim.setStartValue(0.0)
        anim.setKeyValueAt(0.35, 1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.valueChanged.connect(self._on_pulse)
        anim.finished.connect(lambda: self._on_pulse(0.0))
        self._pulse_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return True

    def is_pulsing(self) -> bool:
        """True while the border pulse runs."""
        return self._pulse > 0.0

    def _on_pulse(self, value: Any) -> None:
        self._pulse = float(value)
        if self._pulse <= 0.0:
            self._pulse_anim = None
        self.update()

    # ================================================================== mouse
    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hovered = True
        self._collapse_timer.stop()
        if not self._hiding:
            self._stop_fade()
            self.setWindowOpacity(1.0)
        if not self._dragging and not self._suppress_expand and QApplication.mouseButtons() == Qt.MouseButton.NoButton:
            self._expand_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self._hovered = False
        self._suppress_expand = False
        self._expand_timer.stop()
        if not self._dragging:
            self._collapse_timer.start()
        if not self._hiding and self.isVisible():
            self.setWindowOpacity(REST_OPACITY)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press_global is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        pos = event.globalPosition().toPoint()
        if not self._dragging:
            if (pos - self._press_global).manhattanLength() < QApplication.startDragDistance():
                return
            self._begin_drag(self._press_global)
        self.drag_to(pos)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            dragged = self._dragging
            self._press_global = None
            if dragged:
                self.end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = None
            self.open_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self._expand_timer.stop()
        self.menu_requested.emit(event.globalPos())
        event.accept()

    # ------------------------------------------------------------------ dragging
    def is_dragging(self) -> bool:
        """True while the user drags the pill."""
        return self._dragging

    def _begin_drag(self, press: QPoint) -> None:
        """Start dragging: collapse (instantly) so the persisted collapsed pill is what moves."""
        self._dragging = True
        self._expand_timer.stop()
        self._collapse_timer.stop()
        current = self.target_card_rect()
        if self._expanded:
            self._expanded = False
            self._layout_buttons()
            self.update()
        offset = press - current.topLeft()
        if current.width() != self._card.width() and self._grow_left:
            offset.setX(offset.x() - (current.width() - self._card.width()))
        offset.setX(max(6, min(self._card.width() - 6, offset.x())))
        offset.setY(max(0, min(HEIGHT - 1, offset.y())))
        self._press_offset = offset
        self.drag_to(press)

    def drag_to(self, global_pos: QPoint) -> None:
        """Move the collapsed pill so the grab point follows *global_pos* (no snapping)."""
        top_left = global_pos - self._press_offset
        self._card = QRect(top_left.x(), top_left.y(), self._card.width(), HEIGHT)
        self._apply_geometry(animate=False)

    def end_drag(self) -> None:
        """Finish a drag: snap to a 12 px inset when within 16 px of an edge, then persist."""
        self._dragging = False
        self._suppress_expand = True
        avail = self._screen_avail()
        if avail is not None:
            snapped = snap_rect(self._card, avail)
            self._card = snapped
            self._apply_geometry(animate=True, duration=SNAP_MS)
        self.position_changed.emit()

    # ================================================================== painting
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        alpha = pal.shadow_alpha * (0.85 if pal.is_dark else 1.1)
        paint_shadow(p, QRectF(self.rect()), RADIUS, SHADOW, blur=16.0, offset_y=4.0, alpha=alpha)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        bg = pal.surface_alt if pal.is_dark else pal.surface
        border = pal.border_strong if pal.is_dark else pal.border
        fill_rounded(p, card, RADIUS, bg, border)
        if self._pulse > 0.0:
            glow = QColor(pal.accent)
            glow.setAlphaF(min(1.0, 0.9 * self._pulse))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(glow, 2.0))
            p.drawRoundedRect(card.adjusted(1, 1, -1, -1), RADIUS - 1, RADIUS - 1)

        x = card.left() + LEFT_PAD
        cy = card.center().y()
        glyph_rect = QRectF(x, cy - RING / 2.0, RING, RING)
        self._paint_glyph(p, glyph_rect, pal)
        x += RING + GAP

        mode = self._mode
        text = self.time_text()
        if mode == "paused":
            color = pal.text_muted
        elif mode == "break":
            color = pal.break_
        else:
            color = pal.text
        p.setFont(self._time_font)
        p.setPen(QColor(color))
        tw = self._text_w(text)
        limit = card.right() - x - (RIGHT_PAD if not self._expanded else BUTTON_RIGHT)
        fm = QFontMetrics(self._time_font)
        shown = text if tw <= limit else fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(limit)))
        left_v = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        p.drawText(QRectF(x, card.top(), max(0.0, limit), HEIGHT), left_v, shown)
        x += tw + GAP

        if self._expanded and self._task:
            buttons_left = card.right() - BUTTON_RIGHT - 2 * BUTTON - BUTTON_GAP - GAP
            avail = min(float(TASK_MAX), buttons_left - x)
            if avail >= 24:
                tfm = QFontMetrics(self._task_font)
                task = tfm.elidedText(self._task, Qt.TextElideMode.ElideRight, int(avail))
                p.setFont(self._task_font)
                p.setPen(QColor(pal.text_muted))
                p.drawText(QRectF(x, card.top(), avail, HEIGHT), left_v, task)
        p.end()

    def _paint_glyph(self, p: QPainter, rect: QRectF, pal: Any) -> None:
        mode = self._mode
        dpr = self.devicePixelRatioF()
        if mode == "focus":
            paint_ring(p, rect, self._fraction, pal.accent, pal.track, RING_THICKNESS)
        elif mode == "paused":
            # 12 px pause glyph: two rounded bars.
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.text_muted))
            h, w, gap = 12.0, 3.4, 3.2
            left = rect.center().x() - (2 * w + gap) / 2.0
            top = rect.center().y() - h / 2.0
            p.drawRoundedRect(QRectF(left, top, w, h), 1.2, 1.2)
            p.drawRoundedRect(QRectF(left + w + gap, top, w, h), 1.2, 1.2)
            p.restore()
        elif mode == "break":
            pm = icons.pixmap("coffee", 16, pal.break_, dpr)
            p.drawPixmap(int(rect.center().x() - 8), int(rect.center().y() - 8), pm)
        else:  # done
            p.save()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.success))
            p.drawEllipse(rect)
            p.restore()
            pm = icons.pixmap("check", 12, pal.accent_text if pal.is_dark else "#FFFFFF", dpr)
            p.drawPixmap(int(rect.center().x() - 6), int(rect.center().y() - 6), pm)


# =========================================================================== controller


class PillController(QObject):
    """Owns the :class:`FocusPill` and decides when it is visible (``ctx.pill``).

    Extensions: :attr:`pill`, :meth:`should_show`, :meth:`refresh`, :meth:`restore_position`,
    :meth:`save_position`, :attr:`user_override` (None | True | False).
    """

    visibility_changed = Signal(bool)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._pill = FocusPill(ctx)
        self._override: bool | None = None
        self._showing = False
        self._done_active = False
        self._pulsed_sid: int | None = None
        self._menu: Menu | None = None
        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.setInterval(DONE_MS)
        self._done_timer.timeout.connect(self._end_done)

        pill = self._pill
        pill.open_requested.connect(self.open_main)
        pill.toggle_requested.connect(self._on_toggle)
        pill.menu_requested.connect(self.show_menu)
        pill.position_changed.connect(self.save_position)

        engine = ctx.engine
        engine.state_changed.connect(self._on_state)
        engine.tick.connect(self._on_tick)
        engine.session_started.connect(self._on_session_started)
        engine.session_finished.connect(self._on_session_finished)
        engine.session_extended.connect(lambda _s: self.refresh())
        ctx.events.main_visibility_changed.connect(lambda _v: self._update_visibility())
        ctx.attention.changed.connect(lambda _s: self._update_visibility())
        ctx.settings.changed.connect(self._on_setting)
        ctx.theme.changed.connect(self._on_theme)

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screen_added)  # type: ignore[attr-defined]
            app.screenRemoved.connect(lambda _s: self.restore_position())  # type: ignore[attr-defined]
            for screen in QGuiApplication.screens():
                screen.availableGeometryChanged.connect(lambda _g: self.restore_position())
        PopupStack.instance().set_obstacle(pill)
        self.refresh()
        self.restore_position()
        self._update_visibility()

    # ================================================================== public API
    @property
    def pill(self) -> FocusPill:
        """The pill widget."""
        return self._pill

    @property
    def user_override(self) -> bool | None:
        """Tray toggle: True = force shown, False = hidden by the user, None = automatic."""
        return self._override

    def toggle_user_visible(self) -> None:
        """Tray "Mini timer: Show/Hide" — overrides the automatic rule until the session ends."""
        if str(self._ctx.engine.state) == "idle":
            return
        self._override = not self.is_showing()
        self._update_visibility()

    def is_showing(self) -> bool:
        """True while the pill is (logically) on screen."""
        return self._showing

    def pulse(self) -> None:
        """Pulse the pill border once (2 minutes left); no-op when hidden or with reduce-motion."""
        if self._showing and self._alive():
            self._pill.pulse()

    def should_show(self) -> bool:
        """The visibility rule (spec §1.6) without the 3 s "Done" display."""
        ctx = self._ctx
        if str(ctx.engine.state) == "idle":
            return False
        if self._override is not None:
            return self._override
        if ctx.attention.state() == "quiet":
            return False
        mode = ctx.settings.get("pill_mode")
        if mode == "always":
            return True
        if mode == "hidden":
            window = ctx.window
            try:
                return window is None or not window.is_main_visible()
            except RuntimeError:
                return True
        return False

    def is_done_showing(self) -> bool:
        """True during the 3 s "Done" display after a session ended."""
        return self._done_active

    # ================================================================== display
    def _alive(self) -> bool:
        """False once the pill widget was destroyed (app teardown)."""
        if _is_valid(self._pill):
            return True
        self._done_timer.stop()
        return False

    def refresh(self) -> None:
        """Push the engine's current state into the pill."""
        if not self._alive():
            return
        if self._done_active:
            self._pill.set_display("done", collapsed="Done", expanded="Done", task=self._pill.task_text())
            return
        ctx = self._ctx
        engine = ctx.engine
        state = str(engine.state)
        show_seconds = bool(ctx.settings.get("pill_show_seconds"))
        if state == "break":
            remaining = engine.break_remaining_seconds
            total = max(1, engine.break_total_seconds)
            self._pill.set_display(
                "break",
                collapsed=pill_time_text(remaining, show_seconds),
                expanded=fmt_clock(remaining),
                task=engine.break_label or "Break",
                fraction=remaining / total,
            )
            return
        if state not in ("focus", "paused"):
            return
        remaining = engine.remaining_seconds
        planned = max(1, engine.planned_seconds)
        label = ""
        if state == "paused":
            checkins = ctx.checkins
            if checkins is not None and getattr(checkins, "paused_by_misses", False):
                label = MISSED_TEXT
        self._pill.set_display(
            state,
            collapsed=pill_time_text(remaining, show_seconds),
            expanded=fmt_clock(remaining),
            task=engine.task,
            fraction=remaining / planned,
            label=label,
        )

    def _on_tick(self) -> None:
        engine = self._ctx.engine
        if str(engine.state) == "focus" and engine.session_id is not None:
            left = engine.remaining_whole_seconds
            if 0 < left <= PULSE_SECONDS and self._pulsed_sid != engine.session_id:
                self._pulsed_sid = engine.session_id
                self.pulse()
        if self._showing:
            self.refresh()

    # ================================================================== visibility
    def _update_visibility(self) -> None:
        if not self._alive():
            return
        want = self._done_active or self.should_show()
        if want == self._showing:
            return
        self._showing = want
        if want:
            self.refresh()
            self._pill.show_pill()
        else:
            self._pill.hide_pill()
        self.visibility_changed.emit(want)

    def _on_state(self, state: str) -> None:
        state = str(state)
        if state == "idle":
            self._override = None
        self.refresh()
        self._update_visibility()

    def _on_session_started(self, _sid: int) -> None:
        self._override = None
        self._end_done(update=False)
        self.refresh()
        self._update_visibility()

    def _on_session_finished(self, _sid: int, outcome: str, restored: bool) -> None:
        self._override = None
        if restored or str(outcome) not in ("completed", "ended_early") or not self._showing or not self._alive():
            return
        self._done_active = True
        self._pill.set_expanded(False, animate=False)
        self.refresh()
        self._done_timer.start()

    def _end_done(self, update: bool = True) -> None:
        if not self._done_active:
            return
        self._done_active = False
        self._done_timer.stop()
        self.refresh()
        if update:
            self._update_visibility()

    def _on_theme(self) -> None:
        if self._alive():
            self._pill.update()

    def _on_setting(self, key: str) -> None:
        if key == "pill_mode":
            self._update_visibility()
        elif key == "pill_show_seconds":
            self.refresh()

    # ================================================================== actions
    def open_main(self) -> None:
        """Double-click / open button: show the main window (activated)."""
        window = self._ctx.window
        if window is not None:
            window.show_main(activate=True)

    def _on_toggle(self) -> None:
        engine = self._ctx.engine
        state = str(engine.state)
        if state == "break":
            engine.skip_break()
        else:
            engine.toggle_pause()

    def build_menu(self) -> Menu:
        """The right-click menu: Pause/Resume, +5 min, Finish, Open Madhura, Hide mini timer."""
        engine = self._ctx.engine
        state = str(engine.state)
        menu = Menu(self._pill)
        if state == "break":
            menu.add_action("End break", engine.skip_break, icon="skip")
        else:
            paused = state == "paused"
            menu.add_action(
                "Resume" if paused else "Pause",
                engine.toggle_pause,
                icon="play" if paused else "pause",
                enabled=engine.is_active,
            )
            menu.add_action("+5 min", lambda: engine.extend(5), icon="plus", enabled=engine.is_active)
            menu.add_action("Finish", engine.finish, icon="check", enabled=engine.is_active)
        menu.add_separator()
        menu.add_action("Open Madhura", self.open_main, icon="expand")
        menu.add_action("Hide mini timer", self._hide_by_user, icon="eye_off")
        return menu

    def show_menu(self, global_pos: QPoint) -> None:
        """Pop the context menu up at *global_pos*."""
        menu = self.build_menu()
        self._menu = menu
        menu.popup(global_pos)

    def _hide_by_user(self) -> None:
        if self._showing:
            self._override = False
            self._update_visibility()

    # ================================================================== position
    def _screens(self) -> list[tuple[str, QRect]]:
        return [(s.name(), s.availableGeometry()) for s in QGuiApplication.screens()]

    def restore_position(self) -> None:
        """Place the pill from kv 'pill_pos' (fallback: bottom-right of the primary screen)."""
        primary = QGuiApplication.primaryScreen()
        if primary is None or not self._alive():
            return
        try:
            data = self._ctx.repo.kv_get(POS_KEY)
        except sqlite3.Error:
            data = None
        width = self._pill.collapsed_width()
        rect = position_from_kv(data, width, self._screens(), primary.availableGeometry())
        self._pill.set_collapsed_card(rect)

    def save_position(self) -> None:
        """Persist the collapsed pill position relative to its screen's available geometry."""
        if not self._alive():
            return
        card = self._pill.collapsed_card()
        screen = QGuiApplication.screenAt(card.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        try:
            self._ctx.repo.kv_set(POS_KEY, position_to_kv(card, screen.name(), screen.availableGeometry()))
        except sqlite3.Error:
            log.exception("Could not save the mini timer position")

    def _on_screen_added(self, screen: QScreen) -> None:
        screen.availableGeometryChanged.connect(lambda _g: self.restore_position())
        self.restore_position()
