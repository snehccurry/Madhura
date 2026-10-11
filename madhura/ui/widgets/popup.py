"""Non-activating floating panels and the stack that lays them out (spec §7.3).

:class:`FloatingPanel` is ALWAYS parentless (kept alive by its controller), never takes focus and
is never raised/activated. :class:`PopupStack` (singleton) stacks presented panels in the
bottom-right corner of the target screen, above the obstacle (the mini timer pill) when they would
overlap, shows at most three at a time and keeps nudges exclusive.

The shadow is a blurred 9-slice pixmap cached per (radius, margin, blur, offset, alpha, dpr), so
resizing a panel never re-blurs.
"""

from __future__ import annotations

import itertools
import sys
from typing import Callable, Iterable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPaintEvent, QPixmap, QScreen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QVBoxLayout,
    QWidget,
)

from madhura.ui import animations, theme
from madhura.ui.widgets._common import fill_rounded

try:  # shiboken6 ships with PySide6
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover
    def _is_valid(_obj: object) -> bool:
        return True


__all__ = [
    "SHADOW_MARGIN",
    "PANEL_WIDTH",
    "PANEL_PADDING",
    "PANEL_RADIUS",
    "PRIORITIES",
    "MAX_VISIBLE",
    "GAP",
    "EDGE_MARGIN",
    "FloatingPanel",
    "PopupStack",
    "compute_layout",
    "shadow_pixmap",
    "paint_shadow",
    "reassert_topmost",
]

SHADOW_MARGIN = 16
PANEL_WIDTH = 340
PANEL_PADDING = 16
PANEL_RADIUS = 14
PRIORITIES: dict[str, int] = {"completion": 0, "checkin": 1, "away": 2, "habit": 3, "nudge": 4}
NUDGE_PRIORITY = PRIORITIES["nudge"]
MAX_VISIBLE = 3
GAP = 10
EDGE_MARGIN = 16
TOPMOST_INTERVAL_MS = 5000


# --------------------------------------------------------------------------- platform helper


def reassert_topmost(widget: QWidget) -> None:
    """Windows only: re-assert HWND_TOPMOST without activating (other OSes: no-op)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        swp_nosize, swp_nomove, swp_noactivate = 0x0001, 0x0002, 0x0010
        ctypes.windll.user32.SetWindowPos(  # type: ignore[attr-defined]
            ctypes.c_void_p(int(widget.winId())),
            ctypes.c_void_p(-1),  # HWND_TOPMOST
            0,
            0,
            0,
            0,
            swp_nosize | swp_nomove | swp_noactivate,
        )
    except (OSError, AttributeError, ValueError, TypeError):
        pass


# --------------------------------------------------------------------------- shadow

_shadow_cache: dict[tuple[float, int, float, float, float, float], QPixmap] = {}


def _blur(image: QImage, radius: float) -> QImage:
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    rect = QRectF(0, 0, image.width(), image.height())
    scene.render(p, rect, rect)
    p.end()
    return out


def shadow_pixmap(
    radius: float,
    margin: int,
    *,
    blur: float = 24.0,
    offset_y: float = 8.0,
    alpha: float = 0.45,
    dpr: float = 1.0,
) -> QPixmap:
    """9-slice source for a soft drop shadow around a rounded rect (cached).

    The source is ``2 * (margin + core) + 1`` logical px square where ``core = radius + 2``.
    """
    key = (round(radius, 2), int(margin), round(blur, 2), round(offset_y, 2), round(alpha, 3), round(dpr, 3))
    cached = _shadow_cache.get(key)
    if cached is not None:
        return cached
    core = int(radius) + 2
    side = 2 * (margin + core) + 1
    px = max(1, int(round(side * dpr)))
    src = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    src.fill(Qt.GlobalColor.transparent)
    p = QPainter(src)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, int(255 * max(0.0, min(1.0, alpha)))))
    # Inset the casting shape a little (negative spread) so the blur fits inside the margin.
    spread = min(4.0, margin * 0.25)
    shape = QRectF(margin + spread, margin + spread + offset_y * 0.5, side - 2 * (margin + spread), side - 2 * (margin + spread))
    p.scale(dpr, dpr)
    p.drawRoundedRect(shape, radius, radius)
    p.end()
    pm = QPixmap.fromImage(_blur(src, blur * 0.5 * dpr))
    pm.setDevicePixelRatio(dpr)
    if len(_shadow_cache) > 64:
        _shadow_cache.clear()
    _shadow_cache[key] = pm
    return pm


def paint_shadow(
    painter: QPainter,
    outer: QRectF | QRect,
    radius: float,
    margin: int,
    *,
    blur: float = 24.0,
    offset_y: float = 8.0,
    alpha: float | None = None,
    dpr: float | None = None,
) -> None:
    """Draw a cached 9-slice shadow filling ``outer`` (= card rect grown by ``margin``)."""
    pal = theme.palette()
    a = pal.shadow_alpha if alpha is None else alpha
    ratio = dpr if dpr is not None else (painter.device().devicePixelRatioF() if painter.device() else 1.0)
    pm = shadow_pixmap(radius, margin, blur=blur, offset_y=offset_y, alpha=a, dpr=ratio)
    o = QRectF(outer)
    core = int(radius) + 2
    c = margin + core  # logical corner slice
    side = 2 * c + 1
    w, h = o.width(), o.height()
    if w < 2 * c + 1 or h < 2 * c + 1:
        painter.drawPixmap(o, pm, QRectF(0, 0, side * ratio, side * ratio))
        return
    sx = [0.0, c, c + 1.0, float(side)]
    tx = [o.left(), o.left() + c, o.right() - c, o.right()]
    ty = [o.top(), o.top() + c, o.bottom() - c, o.bottom()]
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for i in range(3):
        for j in range(3):
            if i == 1 and j == 1:
                continue  # the centre is covered by the opaque card
            src = QRectF(sx[i] * ratio, sx[j] * ratio, (sx[i + 1] - sx[i]) * ratio, (sx[j + 1] - sx[j]) * ratio)
            dst = QRectF(tx[i], ty[j], tx[i + 1] - tx[i], ty[j + 1] - ty[j])
            if dst.width() > 0 and dst.height() > 0:
                painter.drawPixmap(dst, pm, src)
    painter.restore()


# --------------------------------------------------------------------------- layout math


def compute_layout(
    avail: QRect,
    sizes: Iterable[QSize],
    obstacle: QRect | None = None,
    *,
    margin: int = EDGE_MARGIN,
    gap: int = GAP,
) -> list[QRect]:
    """Card rectangles for ``sizes`` (bottom → top) anchored bottom-right of ``avail``.

    When ``obstacle`` overlaps the column, the column starts ``gap`` px above it; if that would
    run off the top of the screen, the column moves to the left of the obstacle instead.
    """
    sizes = list(sizes)
    if not sizes:
        return []
    right = avail.x() + avail.width() - margin
    bottom = avail.y() + avail.height() - margin
    col_w = max(s.width() for s in sizes)
    total_h = sum(s.height() for s in sizes) + gap * (len(sizes) - 1)

    def column_rect(r: int, b: int) -> QRect:
        return QRect(r - col_w, b - total_h, col_w, total_h)

    if obstacle is not None and not obstacle.isNull():
        if column_rect(right, bottom).intersects(obstacle):
            above = obstacle.y() - gap
            if above - total_h >= avail.y() + margin:
                bottom = min(bottom, above)
            else:
                right = obstacle.x() - gap
    rects: list[QRect] = []
    y = bottom
    for s in sizes:
        rects.append(QRect(right - s.width(), y - s.height(), s.width(), s.height()))
        y -= s.height() + gap
    return rects


# --------------------------------------------------------------------------- panel


class FloatingPanel(QWidget):
    """Parentless, non-activating, always-on-top rounded panel with a soft shadow.

    ``kind`` selects the stacking priority (completion/checkin/away/habit/nudge). Use
    :meth:`set_content` to place (or swap) the content widget, :meth:`present` to show it via the
    :class:`PopupStack` and :meth:`dismiss` to animate it away. ``closed`` fires once per
    present/dismiss cycle; ``hover_changed(bool)`` reports pointer enter/leave.
    """

    closed = Signal()
    hover_changed = Signal(bool)

    def __init__(
        self,
        kind: str = "panel",
        *,
        width: int = PANEL_WIDTH,
        padding: int = PANEL_PADDING,
        radius: float = PANEL_RADIUS,
        margin: int = SHADOW_MARGIN,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.kind = kind
        self.priority = PRIORITIES.get(kind, 2)
        self.seq = 0
        self.shadow_margin = margin
        self.padding = padding
        self.radius = radius
        self._card_width = width
        self._content: QWidget | None = None
        self._geom_anim: QAbstractAnimation | None = None
        self._fade_anim: QAbstractAnimation | None = None
        self._closing = False
        self._open = False  # between present()/show and closed
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(margin + padding, margin + padding, margin + padding, margin + padding)
        self._layout.setSpacing(0)
        self.resize(width + 2 * margin, 2 * (margin + padding))
        self._topmost = QTimer(self)
        self._topmost.setInterval(TOPMOST_INTERVAL_MS)
        self._topmost.timeout.connect(lambda: reassert_topmost(self))

    # ------------------------------------------------------------------ content
    def content(self) -> QWidget | None:
        """The current content widget."""
        return self._content

    def set_content(self, widget: QWidget, animate: bool = True) -> None:
        """Place ``widget`` as the content. If visible, the card resizes keeping its bottom edge
        fixed (grows upward, 180 ms)."""
        old = self._content
        if old is widget:
            return
        if old is not None:
            self._layout.removeWidget(old)
            old.hide()
            old.setParent(None)  # type: ignore[call-overload]
        self._content = widget
        widget.setParent(self)
        self._layout.addWidget(widget)
        widget.show()
        self.disable_child_focus()
        self._layout.activate()
        if self.isVisible():
            self.sync_size(animate=animate, duration=180)
        else:
            self.resize(self._card_width + 2 * self.shadow_margin, self.desired_height())

    def card_width(self) -> int:
        """Nominal card width used by the stack (the window may be animated wider/narrower)."""
        return self._card_width

    def set_card_width(self, width: int, resize: bool = True) -> None:
        """Change the nominal card width (window width = width + 2 × shadow margin)."""
        self._card_width = int(width)
        if resize:
            g = self.geometry()
            self.resize(self._card_width + 2 * self.shadow_margin, g.height())

    def disable_child_focus(self) -> None:
        """Panels never take focus: make every child button NoFocus."""
        for b in self.findChildren(QAbstractButton):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ------------------------------------------------------------------ geometry
    def desired_height(self) -> int:
        """Window height needed for the content at the current width."""
        self.ensurePolished()  # QSS fonts/margins of the children change their size hints
        if not self.isVisible():
            # Before the first show Qt keeps stale cached size hints for nested containers whose
            # children were shown/hidden or re-texted (e.g. the check-in intro line), so the
            # stack would lay out a card that then grows downward over the pill.
            for child in self.findChildren(QWidget):
                if child.layout() is not None:
                    child.updateGeometry()
        self._layout.activate()
        if self._layout.hasHeightForWidth():
            h = self._layout.totalHeightForWidth(self._card_width + 2 * self.shadow_margin)
        else:
            h = self._layout.totalSizeHint().height()
        return max(h, 2 * (self.shadow_margin + self.padding))

    def card_size(self) -> QSize:
        """Visual card size (excludes the shadow margin).

        Never narrower than the content's minimum: Qt widens a window to its minimum size, so a
        card laid out at the nominal width would otherwise run past the screen's right edge.
        """
        minimum = self.minimumSizeHint().width() - 2 * self.shadow_margin
        return QSize(max(self._card_width, minimum), self.desired_height() - 2 * self.shadow_margin)

    def card_rect(self) -> QRect:
        """Card rectangle in widget coordinates."""
        m = self.shadow_margin
        return self.rect().adjusted(m, m, -m, -m)

    def visual_geometry(self) -> QRect:
        """Card rectangle in global coordinates."""
        m = self.shadow_margin
        return self.geometry().adjusted(m, m, -m, -m)

    def window_rect_for_card(self, card: QRect) -> QRect:
        """Window geometry whose card lands on ``card``."""
        m = self.shadow_margin
        return card.adjusted(-m, -m, m, m)

    def sync_size(self, animate: bool = True, duration: int = 180) -> None:
        """Re-fit the height to the content (bottom edge fixed)."""
        stack = PopupStack.instance_if_exists()
        if stack is not None and stack.is_visible_panel(self):
            stack.relayout(animate=animate, duration=duration)
            return
        g = self.geometry()
        h = self.desired_height()
        if h == g.height():
            return
        target = QRect(g.x(), g.y() + g.height() - h, g.width(), h)
        self.move_to(target, animate=animate, duration=duration)

    # ------------------------------------------------------------------ motion
    def _stop_anim(self) -> None:
        for attr in ("_geom_anim", "_fade_anim"):
            anim = getattr(self, attr)
            setattr(self, attr, None)
            if anim is not None:
                try:
                    anim.stop()
                except RuntimeError:
                    pass

    def _stop_geom(self) -> None:
        anim = self._geom_anim
        self._geom_anim = None
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass

    def _geometry_anim(self, prop: bytes, start: object, end: object, ms: int, curve: QEasingCurve.Type) -> None:
        self._stop_geom()
        anim = QPropertyAnimation(self, prop, self)
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.finished.connect(lambda a=anim: self._geom_done(a))
        self._geom_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _geom_done(self, anim: QAbstractAnimation) -> None:
        if self._geom_anim is anim:
            self._geom_anim = None

    def is_animating(self) -> bool:
        """True while a fade or move animation runs."""
        return self._geom_anim is not None or self._fade_anim is not None

    def move_to(self, geometry: QRect, animate: bool = True, duration: int = 200) -> None:
        """Move/resize the window to ``geometry`` (animated 200 ms OutCubic). Never interrupts a
        running fade."""
        if geometry == self.geometry() and self._geom_anim is None:
            return
        if not animate or not animations.motion_enabled() or not self.isVisible():
            self._stop_geom()
            self.setGeometry(geometry)
            return
        self._geometry_anim(b"geometry", self.geometry(), geometry, duration, QEasingCurve.Type.OutCubic)

    def _fade(self, start: float, end: float, ms: int, curve: QEasingCurve.Type, on_done: Callable[[], None]) -> None:
        old = self._fade_anim
        self._fade_anim = None
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.setStartValue(start)
        anim.setEndValue(end)

        def done() -> None:
            if self._fade_anim is anim:
                self._fade_anim = None
                on_done()

        anim.finished.connect(done)
        self._fade_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def animate_in(self, geometry: QRect) -> None:
        """Show at ``geometry``: slide up 16 px + fade in (220 ms OutCubic); opacity-only
        (100 ms) with reduce-motion."""
        self._stop_anim()
        self._closing = False
        self._open = True
        self.disable_child_focus()
        motion = animations.motion_enabled()
        start = QRect(geometry).translated(0, 16) if motion else QRect(geometry)
        self.setGeometry(start)
        self.setWindowOpacity(0.0)
        self.show()
        self._fade(0.0, 1.0, 220 if motion else 100, QEasingCurve.Type.OutCubic, lambda: None)
        if motion:
            self._geometry_anim(b"geometry", start, geometry, 220, QEasingCurve.Type.OutCubic)

    def animate_out(self, on_done: Callable[[], None] | None = None) -> None:
        """Fade out + drift down 8 px (160 ms InCubic), then hide and call ``on_done``;
        opacity-only (100 ms) with reduce-motion."""
        self._stop_anim()
        self._closing = True
        if not self.isVisible():
            self._finish_hide(on_done)
            return
        motion = animations.motion_enabled()
        self._fade(self.windowOpacity(), 0.0, 160 if motion else 100, QEasingCurve.Type.InCubic, lambda: self._finish_hide(on_done))
        if motion:
            self._geometry_anim(b"pos", self.pos(), self.pos() + QPoint(0, 8), 160, QEasingCurve.Type.InCubic)

    def _finish_hide(self, on_done: Callable[[], None] | None) -> None:
        self._stop_geom()
        self.hide()
        self.setWindowOpacity(1.0)
        self._closing = False
        if on_done is not None:
            on_done()

    def is_closing(self) -> bool:
        """True while the exit animation runs."""
        return self._closing

    # ------------------------------------------------------------------ presenting
    def present(self, priority: int | None = None) -> None:
        """Show through the :class:`PopupStack` (queued if needed)."""
        PopupStack.instance().push(self, priority)

    def dismiss(self, animate: bool = True) -> None:
        """Animate away and emit ``closed`` (also removes a queued panel)."""
        stack = PopupStack.instance_if_exists()
        if stack is not None and stack.contains(self):
            stack.remove(self, animate=animate)
            return
        if self.isVisible() and not self._closing:
            if animate:
                self.animate_out(self._emit_closed)
            else:
                self._stop_anim()
                self.hide()
                self._emit_closed()

    def _emit_closed(self) -> None:
        self._open = False
        self.closed.emit()

    def is_open(self) -> bool:
        """True from present()/show until ``closed``."""
        return self._open

    # ------------------------------------------------------------------ events
    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self._open = True
        if sys.platform == "win32":
            reassert_topmost(self)
            self._topmost.start()

    def hideEvent(self, event: QEvent) -> None:  # noqa: N802
        self._topmost.stop()
        super().hideEvent(event)
        if event.spontaneous() or self._closing:
            return
        stack = PopupStack.instance_if_exists()
        if stack is not None and stack.is_visible_panel(self):
            # Hidden directly (not via dismiss()): let the stack forget it so a relayout never
            # brings it back.
            stack.forget(self)

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self.hover_changed.emit(True)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self.hover_changed.emit(False)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.LayoutRequest and self.isVisible() and not self._closing:
            QTimer.singleShot(0, self, self._on_layout_request)
        return super().event(e)

    def _on_layout_request(self) -> None:
        if not (_is_valid(self) and self.isVisible() and not self._closing):
            return
        stack = PopupStack.instance_if_exists()
        if stack is not None and stack.is_visible_panel(self):
            # The window may already have grown (downward) to its layout's minimum size, so
            # compare with where the stack wants the card, not with the current height.
            target = next((g for p, g in stack.target_geometries() if p is self), None)
            if self._geom_anim is None and target == self.geometry():
                return
            stack.relayout(animate=True, duration=180)
            return
        target_h = self.desired_height()
        if self._geom_anim is None and target_h == self.height():
            return
        self.sync_size(animate=True)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_shadow(p, QRectF(self.rect()), self.radius, self.shadow_margin)
        bg = pal.surface_alt if pal.is_dark else pal.surface
        border = pal.border_strong if pal.is_dark else pal.border
        fill_rounded(p, QRectF(self.card_rect()), self.radius, bg, border)
        p.end()


# --------------------------------------------------------------------------- stack


class PopupStack(QObject):
    """Singleton that positions presented :class:`FloatingPanel` s (see module docstring)."""

    changed = Signal()

    _instance: "PopupStack | None" = None

    @classmethod
    def instance(cls) -> "PopupStack":
        """The application-wide stack (created on first use)."""
        inst = cls._instance
        if inst is None or not _is_valid(inst):
            inst = cls(QApplication.instance())
            cls._instance = inst
        return inst

    @classmethod
    def instance_if_exists(cls) -> "PopupStack | None":
        """The stack if it was already created."""
        inst = cls._instance
        return inst if inst is not None and _is_valid(inst) else None

    @classmethod
    def reset(cls) -> None:
        """Hide every panel and drop the singleton (tests)."""
        inst = cls.instance_if_exists()
        if inst is not None:
            panels = list(inst._visible) + list(inst._queue)
            inst._visible.clear()
            inst._queue.clear()
            for p in panels:
                if _is_valid(p):
                    p._stop_anim()  # noqa: SLF001
                    p.hide()
            inst.set_obstacle(None)
            inst.deleteLater()
        cls._instance = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._visible: list[FloatingPanel] = []
        self._queue: list[FloatingPanel] = []
        self._seq = itertools.count(1)
        self._obstacle: QWidget | None = None
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screen_added)  # type: ignore[attr-defined]
            app.screenRemoved.connect(self._on_screens_changed)  # type: ignore[attr-defined]
            for s in QGuiApplication.screens():
                self._watch_screen(s)

    # ------------------------------------------------------------------ queries
    def visible_panels(self) -> list[FloatingPanel]:
        """Shown panels, bottom → top."""
        self._purge()
        return sorted(self._visible, key=lambda p: (p.priority, p.seq))

    def queued_panels(self) -> list[FloatingPanel]:
        """Waiting panels in promotion order."""
        self._purge()
        return list(self._queue)

    def contains(self, panel: FloatingPanel) -> bool:
        """True if ``panel`` is shown or queued."""
        return panel in self._visible or panel in self._queue

    def is_visible_panel(self, panel: FloatingPanel) -> bool:
        """True if ``panel`` is currently shown by the stack."""
        return panel in self._visible

    def obstacle(self) -> QWidget | None:
        """Current obstacle widget."""
        return self._obstacle

    # ------------------------------------------------------------------ commands
    def push(self, panel: FloatingPanel, priority: int | None = None) -> None:
        """Show ``panel`` (or queue it: max 3 visible; nudges only when alone)."""
        self._purge()
        if priority is not None:
            panel.priority = int(priority)
        if panel in self._visible:
            self.relayout(animate=True)
            return
        if panel in self._queue:
            return
        panel.seq = next(self._seq)
        panel._open = True  # noqa: SLF001
        is_nudge = panel.priority >= NUDGE_PRIORITY
        if is_nudge:
            if self._visible or any(q.priority < NUDGE_PRIORITY for q in self._queue):
                self._queue.append(panel)
                self.changed.emit()
                return
        else:
            for nudge in [p for p in self._visible if p.priority >= NUDGE_PRIORITY]:
                self._visible.remove(nudge)
                nudge.animate_out()
                self._queue.append(nudge)
        if len(self._visible) >= MAX_VISIBLE:
            self._queue.append(panel)
            self.changed.emit()
            return
        self._visible.append(panel)
        self.relayout(animate=True, entering=panel)
        self.changed.emit()

    def remove(self, panel: FloatingPanel, animate: bool = True) -> None:
        """Dismiss ``panel`` (animated) and promote queued panels."""
        if panel in self._queue:
            self._queue.remove(panel)
            panel._open = False  # noqa: SLF001
            panel.closed.emit()
            self.changed.emit()
            return
        if panel not in self._visible:
            return
        self._visible.remove(panel)

        def done() -> None:
            panel._open = False  # noqa: SLF001
            panel.closed.emit()

        if animate:
            panel.animate_out(done)
        else:
            panel._stop_anim()  # noqa: SLF001
            panel.hide()
            done()
        self._promote()
        self.relayout(animate=animate)
        self.changed.emit()

    def forget(self, panel: FloatingPanel) -> None:
        """Drop ``panel`` without animating it (it was hidden by its owner); emits ``closed``."""
        if panel in self._queue:
            self._queue.remove(panel)
        elif panel in self._visible:
            self._visible.remove(panel)
            self._promote()
            self.relayout(animate=True)
        else:
            return
        panel._open = False  # noqa: SLF001
        panel.closed.emit()
        self.changed.emit()

    def set_obstacle(self, widget: QWidget | None) -> None:
        """Register the widget panels must avoid (the pill); relayouts when it moves/shows/hides."""
        if self._obstacle is widget:
            return
        if self._obstacle is not None and _is_valid(self._obstacle):
            self._obstacle.removeEventFilter(self)
        self._obstacle = widget
        if widget is not None:
            widget.installEventFilter(self)
        self.relayout(animate=True)

    def _promote(self) -> None:
        while len(self._visible) < MAX_VISIBLE and self._queue:
            regular = [p for p in self._queue if p.priority < NUDGE_PRIORITY]
            if regular:
                nxt = regular[0]
            elif not self._visible:
                nxt = self._queue[0]
            else:
                break
            self._queue.remove(nxt)
            self._visible.append(nxt)
            self.relayout(animate=True, entering=nxt)
            if nxt.priority >= NUDGE_PRIORITY:
                break

    # ------------------------------------------------------------------ layout
    def target_screen(self) -> QScreen | None:
        """Obstacle's screen if visible, else the screen under the cursor, else primary."""
        ob = self._obstacle
        if ob is not None and _is_valid(ob) and ob.isVisible():
            s = ob.screen()
            if s is not None:
                return s
        s = QGuiApplication.screenAt(QCursor.pos())
        return s or QGuiApplication.primaryScreen()

    def _obstacle_rect(self, screen: QScreen | None) -> QRect | None:
        ob = self._obstacle
        if ob is None or not _is_valid(ob) or not ob.isVisible():
            return None
        if screen is not None and ob.screen() is not screen:
            return None
        if isinstance(ob, FloatingPanel):
            return ob.visual_geometry()
        return ob.frameGeometry()

    def target_geometries(self) -> list[tuple[FloatingPanel, QRect]]:
        """(panel, window geometry) for every shown panel, bottom → top."""
        screen = self.target_screen()
        if screen is None:
            return []
        panels = self.visible_panels()
        rects = compute_layout(screen.availableGeometry(), [p.card_size() for p in panels], self._obstacle_rect(screen))
        return [(p, p.window_rect_for_card(r)) for p, r in zip(panels, rects)]

    def relayout(self, animate: bool = True, entering: FloatingPanel | None = None, duration: int = 200) -> None:
        """Re-position all shown panels (animated 200 ms)."""
        self._purge()
        for panel, geo in self.target_geometries():
            if panel is entering or (not panel.isVisible() and not panel.is_closing()):
                panel.animate_in(geo)
            else:
                panel.move_to(geo, animate=animate, duration=duration)

    # ------------------------------------------------------------------ plumbing
    def _purge(self) -> None:
        self._visible = [p for p in self._visible if _is_valid(p)]
        self._queue = [p for p in self._queue if _is_valid(p)]

    def _watch_screen(self, screen: QScreen) -> None:
        screen.availableGeometryChanged.connect(self._on_screens_changed)

    def _on_screens_changed(self, *_args: object) -> None:
        if self._visible:
            self.relayout(animate=True)

    def _on_screen_added(self, screen: QScreen) -> None:
        self._watch_screen(screen)
        self._on_screens_changed()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._obstacle and event.type() in (
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        ):
            if self._visible:
                QTimer.singleShot(0, lambda: self.relayout(animate=True) if _is_valid(self) else None)
        return False
