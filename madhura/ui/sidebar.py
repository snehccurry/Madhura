"""Sidebar: wordmark, page navigation and a live status footer (spec §8).

216 px wide (1 px right border); below a 1000 px window width the main window collapses it to a
64 px icon rail (180 ms). The footer shows the running session ("23 min · Write report"), the
break ("Break · 4 min") or today's progress toward the daily goal ("1h 20m of 2h today").
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QEnterEvent, QFontMetrics, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QVBoxLayout, QWidget

from madhura.core.timefmt import fmt_duration
from madhura.ui import animations, fonts, icons, theme
from madhura.ui.pages import PAGES
from madhura.ui.shortcuts_sheet import display_keys
from madhura.ui.widgets._common import HoverFader, KeyboardFocusMixin, fill_rounded

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "Sidebar",
    "NavItem",
    "SidebarStatus",
    "paint_ring",
    "EXPANDED_WIDTH",
    "COLLAPSED_WIDTH",
    "COLLAPSE_BELOW",
]

log = logging.getLogger(__name__)

EXPANDED_WIDTH = 216
COLLAPSED_WIDTH = 64
#: Window width below which the sidebar collapses to the icon rail.
COLLAPSE_BELOW = 1000
COLLAPSE_MS = 180
SIDE_PAD = 12
ITEM_HEIGHT = 36
ICON_SIZE = 18
ICON_GAP = 12
WORDMARK_HEIGHT = 56
LOGO_SIZE = 22
STATUS_HEIGHT = 44
STATUS_RING = 18
_RADIUS = 8.0
#: Items narrower than this draw only their icon (collapsed rail / mid-animation).
_LABEL_MIN_WIDTH = 120
#: Ring repaint resolution for the footer (steps per turn).
_RING_STEPS = 72


def paint_ring(
    painter: QPainter,
    rect: QRectF,
    fraction: float,
    color: QColor | str,
    track: QColor | str | None,
    thickness: float,
) -> None:
    """Draw a progress ring inside *rect*: optional full *track*, then an arc of *fraction* (0..1)
    in *color* starting at 12 o'clock and running clockwise, with round caps."""
    half = thickness / 2.0
    r = QRectF(rect).adjusted(half, half, -half, -half)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if track is not None:
        painter.setPen(QPen(QColor(track), thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawEllipse(r)
    frac = max(0.0, min(1.0, float(fraction)))
    if frac > 0.0:
        pen = QPen(QColor(color), thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        if frac >= 0.999:
            painter.drawEllipse(r)
        else:
            painter.drawArc(r, 90 * 16, -int(round(frac * 360 * 16)))
    painter.restore()


# --------------------------------------------------------------------------- wordmark


class _Wordmark(QWidget):
    """22 px accent logo + "Madhura" (15/600); logo only when collapsed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(WORDMARK_HEIGHT)
        self.setFont(fonts.font(15, 600))
        self.setAccessibleName("Madhura")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(LOGO_SIZE, WORDMARK_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(EXPANDED_WIDTH - 2 * SIDE_PAD, WORDMARK_HEIGHT)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        compact = self.width() < _LABEL_MIN_WIDTH
        # Align the logo's centre with the nav icons' centre (item padding 12 + half icon).
        nav_center = ICON_GAP + ICON_SIZE / 2.0
        x = (self.width() - LOGO_SIZE) / 2.0 if compact else nav_center - LOGO_SIZE / 2.0
        y = (self.height() - LOGO_SIZE) / 2.0
        pm = icons.pixmap("logo", LOGO_SIZE, pal.accent, self.devicePixelRatioF())
        p.drawPixmap(int(round(x)), int(round(y)), pm)
        if not compact:
            p.setFont(self.font())
            p.setPen(QColor(pal.text))
            tx = x + LOGO_SIZE + 10
            p.drawText(QRectF(tx, 0, self.width() - tx, self.height()), int(Qt.AlignmentFlag.AlignVCenter), "Madhura")
        p.end()


# --------------------------------------------------------------------------- nav item


class NavItem(KeyboardFocusMixin, QAbstractButton):
    """One navigation entry: 36 px, radius 8, 18 px icon + label; active = accent_soft + accent icon.

    Reaches keyboard focus with Tab (not with the mouse, so clicking never pulls focus out of a
    text field); ↑/↓ move between items, Enter/Space activate.
    """

    def __init__(self, key: str, label: str, icon_name: str, shortcut: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._label = label
        self._icon = icon_name
        self._shortcut = shortcut
        self._hover = HoverFader(self, 120)
        self._ring_radius = _RADIUS
        #: Called with (item, +1/-1) on ↓/↑ (set by the Sidebar).
        self.on_arrow: Callable[[NavItem, int], None] | None = None
        self.setCheckable(True)
        self.setFixedHeight(ITEM_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFont(fonts.font(13, 500))
        self.setText(label)
        self.setAccessibleName(label)
        self.setToolTip(f"{label} · {display_keys(shortcut)}")

    # ------------------------------------------------------------------ API
    def label(self) -> str:
        """Visible label."""
        return self._label

    def set_active(self, active: bool) -> None:
        """Mark this item as the current page."""
        self.setChecked(bool(active))
        self.update()

    def is_active(self) -> bool:
        """True for the current page."""
        return self.isChecked()

    def nextCheckState(self) -> None:  # noqa: N802
        # Clicking never toggles: the Sidebar decides which item is active.
        pass

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(EXPANDED_WIDTH - 2 * SIDE_PAD, ITEM_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(ITEM_HEIGHT, ITEM_HEIGHT)

    # ------------------------------------------------------------------ events
    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            self.click()
            event.accept()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.on_arrow is not None:
            self.on_arrow(self, 1 if key == Qt.Key.Key_Down else -1)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._hover.value
        rect = QRectF(self.rect())
        active = self.isChecked()
        if active:
            fill_rounded(p, rect, _RADIUS, pal.accent_soft)
        elif self.isDown():
            fill_rounded(p, rect, _RADIUS, pal.hover)
        elif t > 0:
            fill_rounded(p, rect, _RADIUS, theme.with_alpha(pal.hover, t))
        compact = self.width() < _LABEL_MIN_WIDTH
        x = (self.width() - ICON_SIZE) / 2.0 if compact else float(ICON_GAP)
        y = (self.height() - ICON_SIZE) / 2.0
        icon_color = QColor(pal.accent) if active else theme.mix(pal.text_muted, pal.text, t)
        pm = icons.pixmap(self._icon, ICON_SIZE, icon_color, self.devicePixelRatioF())
        p.drawPixmap(int(round(x)), int(round(y)), pm)
        if not compact:
            tx = x + ICON_SIZE + ICON_GAP
            avail = int(self.width() - tx - ICON_GAP)
            f = fonts.font(13, 600 if active else 500)
            p.setFont(f)
            p.setPen(QColor(pal.text) if active else theme.mix(pal.text_muted, pal.text, t))
            text = QFontMetrics(f).elidedText(self._label, Qt.TextElideMode.ElideRight, max(0, avail))
            p.drawText(QRectF(tx, 0, avail, self.height()), int(Qt.AlignmentFlag.AlignVCenter), text)
        p.end()


# --------------------------------------------------------------------------- status footer


class SidebarStatus(KeyboardFocusMixin, QAbstractButton):
    """Footer status: 18 px ring + one line. Running "23 min · task", paused "Paused · task",
    break "Break · 4 min", idle "1h 20m of 2h today". Click → Focus page."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._hover = HoverFader(self, 120)
        self._ring_radius = _RADIUS
        self._today_seconds = 0
        self._mode = "idle"
        self._primary = ""
        self._secondary = ""
        self._fraction = 0.0
        self._key: tuple[Any, ...] | None = None
        self.setFixedHeight(STATUS_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        engine = ctx.engine
        engine.tick.connect(self.refresh)
        engine.state_changed.connect(self._on_engine_event)
        engine.session_finished.connect(self._on_engine_event)
        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.events.day_changed.connect(self.refresh_totals)
        ctx.settings.changed.connect(self._on_setting)
        ctx.theme.changed.connect(self.update)
        self.refresh_totals()

    # ------------------------------------------------------------------ data
    def _on_engine_event(self, *_args: object) -> None:
        self.refresh_totals()

    def _on_data_changed(self, kind: str) -> None:
        if kind in ("sessions", "all"):
            self.refresh_totals()

    def _on_setting(self, key: str) -> None:
        if key in ("daily_goal_minutes", "day_start_hour"):
            self.refresh_totals()

    def refresh_totals(self) -> None:
        """Re-read today's counted focus time, then refresh."""
        try:
            sessions = self._ctx.repo.sessions_between(*self._ctx.day_bounds(self._ctx.today()))
            self._today_seconds = int(sum(s.focused_seconds for s in sessions))
        except sqlite3.Error:  # e.g. the repository closed during shutdown
            log.debug("Could not read today's sessions", exc_info=True)
            self._today_seconds = 0
        self.refresh()

    def _compute(self) -> tuple[str, str, str, float]:
        e = self._ctx.engine
        state = str(e.state)
        if state in ("focus", "paused"):
            fraction = e.remaining_seconds / max(1, e.planned_seconds)
            if state == "paused":
                return "paused", "Paused", e.task, fraction
            minutes = max(1, math.ceil(e.remaining_seconds / 60.0 - 1e-6))
            return "focus", f"{minutes} min", e.task, fraction
        if state == "break":
            minutes = max(1, math.ceil(e.break_remaining_seconds / 60.0 - 1e-6))
            fraction = e.break_remaining_seconds / max(1, e.break_total_seconds)
            return "break", f"{e.break_label or 'Break'} · {minutes} min", "", fraction
        goal = int(self._ctx.settings.get("daily_goal_minutes")) * 60
        fraction = self._today_seconds / goal if goal else 0.0
        return "idle", f"{fmt_duration(self._today_seconds)} of {fmt_duration(goal)} today", "", fraction

    def refresh(self) -> None:
        """Recompute the line; repaints only when the text or ring visibly changes."""
        mode, primary, secondary, fraction = self._compute()
        key = (mode, primary, secondary, int(round(max(0.0, min(1.0, fraction)) * _RING_STEPS)))
        if key == self._key:
            return
        self._key = key
        self._mode, self._primary, self._secondary, self._fraction = mode, primary, secondary, fraction
        full = self.status_text()
        self.setToolTip(full + (" · Open Focus" if mode != "idle" else ""))
        self.setAccessibleName(full)
        self.update()

    # ------------------------------------------------------------------ API
    def mode(self) -> str:
        """"focus" | "paused" | "break" | "idle"."""
        return self._mode

    def status_text(self) -> str:
        """The full status line ("23 min · Write report")."""
        return f"{self._primary} · {self._secondary}" if self._secondary else self._primary

    def fraction(self) -> float:
        """Ring fill 0..1 (remaining for sessions/breaks, goal progress when idle)."""
        return self._fraction

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(EXPANDED_WIDTH - 2 * SIDE_PAD, STATUS_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(STATUS_HEIGHT, STATUS_HEIGHT)

    # ------------------------------------------------------------------ events
    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def _ring_colors(self) -> tuple[QColor, QColor]:
        pal = theme.palette()
        if self._mode == "focus":
            return QColor(pal.accent), QColor(pal.track)
        if self._mode == "paused":
            return QColor(pal.text_subtle), QColor(pal.track)
        if self._mode == "break":
            return QColor(pal.break_), QColor(pal.track)
        done = self._fraction >= 1.0
        return QColor(pal.success if done else pal.accent), QColor(pal.track)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._hover.value
        if self.isDown():
            fill_rounded(p, QRectF(self.rect()), _RADIUS, pal.hover)
        elif t > 0:
            fill_rounded(p, QRectF(self.rect()), _RADIUS, theme.with_alpha(pal.hover, t))
        compact = self.width() < _LABEL_MIN_WIDTH
        x = (self.width() - STATUS_RING) / 2.0 if compact else float(ICON_GAP)
        y = (self.height() - STATUS_RING) / 2.0
        color, track = self._ring_colors()
        paint_ring(p, QRectF(x, y, STATUS_RING, STATUS_RING), self._fraction, color, track, 2.5)
        if not compact:
            tx = x + STATUS_RING + 10
            avail = self.width() - tx - ICON_GAP
            bold = fonts.font(12, 600, tabular=True)
            light = fonts.font(12, 400)
            primary_color = QColor(pal.text) if self._mode in ("focus", "break") else theme.mix(pal.text_muted, pal.text, t)
            fm_bold = QFontMetrics(bold)
            primary = fm_bold.elidedText(self._primary, Qt.TextElideMode.ElideRight, int(avail))
            p.setFont(bold if self._mode != "idle" else light)
            p.setPen(primary_color)
            rect = QRectF(tx, 0, avail, self.height())
            p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter), primary)
            if self._secondary:
                used = (fm_bold if self._mode != "idle" else QFontMetrics(light)).horizontalAdvance(primary)
                rest = avail - used
                if rest > 16:
                    fm = QFontMetrics(light)
                    secondary = fm.elidedText(f" · {self._secondary}", Qt.TextElideMode.ElideRight, int(rest))
                    p.setFont(light)
                    p.setPen(QColor(pal.text_muted))
                    p.drawText(QRectF(tx + used, 0, rest, self.height()), int(Qt.AlignmentFlag.AlignVCenter), secondary)
        p.end()


# --------------------------------------------------------------------------- sidebar


class Sidebar(QWidget):
    """Wordmark + four nav items + status footer. ``navigate(key)`` fires on user selection."""

    navigate = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._current = PAGES[0][0]
        self._collapsed = False
        self.setObjectName("mhSidebar")
        self.setFixedWidth(EXPANDED_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SIDE_PAD, 0, SIDE_PAD, SIDE_PAD)
        lay.setSpacing(0)
        self.wordmark = _Wordmark(self)
        lay.addWidget(self.wordmark)
        lay.addSpacing(8)
        self.items: dict[str, NavItem] = {}
        for key, label, icon_name, shortcut in PAGES:
            item = NavItem(key, label, icon_name, shortcut, self)
            item.on_arrow = self._move_focus
            item.clicked.connect(lambda _checked=False, k=key: self._on_item(k))
            self.items[key] = item
            lay.addWidget(item)
            lay.addSpacing(2)
        lay.addStretch(1)
        self.status = SidebarStatus(ctx, self)
        self.status.clicked.connect(lambda: self.navigate.emit("focus"))
        lay.addWidget(self.status)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(COLLAPSE_MS)
        self._anim.valueChanged.connect(lambda v: self.setFixedWidth(int(round(float(v)))))
        ctx.theme.changed.connect(self.update)
        self.set_current(self._current)

    # ------------------------------------------------------------------ navigation
    def _on_item(self, key: str) -> None:
        self.set_current(key)
        self.navigate.emit(key)

    def _move_focus(self, item: NavItem, step: int) -> None:
        keys = list(self.items)
        idx = (keys.index(item.key) + step) % len(keys)
        self.items[keys[idx]].setFocus(Qt.FocusReason.TabFocusReason)

    def set_current(self, key: str) -> None:
        """Highlight *key* (no signal)."""
        if key not in self.items:
            raise KeyError(key)
        self._current = key
        for k, item in self.items.items():
            item.set_active(k == key)

    def current(self) -> str:
        """Highlighted page key."""
        return self._current

    # ------------------------------------------------------------------ collapse
    def set_collapsed(self, collapsed: bool, animate: bool = True) -> None:
        """Switch between the full sidebar and the 64 px icon rail (180 ms)."""
        collapsed = bool(collapsed)
        if collapsed == self._collapsed and self._anim.state() != QVariantAnimation.State.Running:
            return
        self._collapsed = collapsed
        target = COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH
        self._anim.stop()
        for item in self.items.values():
            item.setToolTip(item.label() if collapsed else f"{item.label()} · {display_keys(item._shortcut)}")  # noqa: SLF001
        if not animate or not animations.motion_enabled() or not self.isVisible():
            self.setFixedWidth(target)
            return
        self._anim.setStartValue(float(self.width()))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def is_collapsed(self) -> bool:
        """True in icon-rail mode."""
        return self._collapsed

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(pal.sidebar))
        p.fillRect(self.width() - 1, 0, 1, self.height(), QColor(pal.border))
        p.end()
