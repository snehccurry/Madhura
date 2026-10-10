"""Private painting / interaction helpers shared by the Madhura widgets."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFocusEvent, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from madhura.ui import animations, theme

KEYBOARD_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)

RING_WIDTH = 2.0
RING_OFFSET = 2


def is_keyboard_reason(reason: Qt.FocusReason) -> bool:
    """True when focus arrived by keyboard (Tab/Backtab/Shortcut)."""
    return reason in KEYBOARD_REASONS


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rounded-rect path (radius clamped to half the shorter side)."""
    r = max(0.0, min(radius, rect.width() / 2.0, rect.height() / 2.0))
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


def fill_rounded(
    painter: QPainter,
    rect: QRectF | QRect,
    radius: float,
    fill: QColor | str | None,
    border: QColor | str | None = None,
    border_width: float = 1.0,
) -> None:
    """Fill + optional crisp hairline border (inset by half the border width)."""
    r = QRectF(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if fill is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawPath(rounded_path(r, radius))
    if border is not None and border_width > 0:
        half = border_width / 2.0
        br = r.adjusted(half, half, -half, -half)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(border), border_width))
        painter.drawPath(rounded_path(br, max(0.0, radius - half)))
    painter.restore()


def elide(text: str, metrics: QFontMetrics, width: int) -> str:
    """Right-elide ``text`` to ``width`` pixels."""
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(width)))


# --------------------------------------------------------------------------- hover fader


class HoverFader(QObject):
    """Animates a 0..1 value (hover, press, check…) and repaints the owner widget."""

    def __init__(self, widget: QWidget, ms: int = 140, value: float = 0.0) -> None:
        super().__init__(widget)
        self._widget = widget
        self._ms = ms
        self._value = float(value)
        self._target = float(value)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(ms)
        self._anim.valueChanged.connect(self._on_value)

    @property
    def value(self) -> float:
        """Current animated value."""
        return self._value

    def _on_value(self, v: object) -> None:
        self._value = float(v)  # type: ignore[arg-type]
        self._widget.update()

    def set_target(self, target: float, animate: bool = True) -> None:
        """Move towards ``target`` (instantly with reduce-motion or ``animate=False``)."""
        target = float(target)
        if target == self._target and (self._anim.state() == QVariantAnimation.State.Running or self._value == target):
            return
        self._target = target
        self._anim.stop()
        if not animate or not animations.motion_enabled():
            self._value = target
            self._widget.update()
            return
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.start()


# --------------------------------------------------------------------------- focus ring


class _FocusRing(QWidget):
    """Transparent overlay that paints a 2 px accent ring 2 px outside a target rect.

    It is parented to the nearest ancestor of the target that has room for the ring (so a
    zero-margin container never clips it), but never escapes a scroll area's content widget.
    """

    def __init__(self) -> None:
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("mhFocusRing")
        self._target: QWidget | None = None
        self._watched: list[QWidget] = []
        self._radius = 8.0
        self._sub: QRect | None = None

    @staticmethod
    def _margin() -> int:
        return RING_OFFSET + int(RING_WIDTH)

    def _ring_rect_in(self, target: QWidget, host: QWidget) -> QRect:
        base = self._sub if self._sub is not None else target.rect()
        top_left = target.mapTo(host, base.topLeft())
        m = self._margin()
        return QRect(top_left, base.size()).adjusted(-m, -m, m, m)

    def _pick_host(self, target: QWidget) -> tuple[QWidget, list[QWidget]]:
        host = target.parentWidget()
        chain: list[QWidget] = [target]
        assert host is not None
        while True:
            if host.rect().contains(self._ring_rect_in(target, host)) or host.isWindow():
                return host, chain
            parent = host.parentWidget()
            if parent is None or parent.objectName() == "qt_scrollarea_viewport":
                return host, chain
            chain.append(host)
            host = parent

    def attach(self, target: QWidget, radius: float, sub_rect: QRect | None = None) -> None:
        if target.parentWidget() is None:
            return
        self._unwatch()
        self._target = target
        self._radius = radius
        self._sub = QRect(sub_rect) if sub_rect is not None else None
        host, chain = self._pick_host(target)
        if self.parentWidget() is not host:
            self.setParent(host)
        for w in chain:
            w.installEventFilter(self)
        self._watched = chain
        self.sync()
        self.raise_()

    def _unwatch(self) -> None:
        for w in self._watched:
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched = []

    def detach(self, target: QWidget | None = None) -> None:
        if target is not None and target is not self._target:
            return
        self._unwatch()
        self._target = None
        self.hide()

    def sync(self) -> None:
        t = self._target
        host = self.parentWidget()
        if t is None or host is None:
            return
        try:
            self.setGeometry(self._ring_rect_in(t, host))
            self.setVisible(t.isVisible())
        except RuntimeError:
            self.detach()
            return
        self.update()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj in self._watched:
            et = event.type()
            if et in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
                self.sync()
                self.raise_()
            elif et == QEvent.Type.Hide or (et == QEvent.Type.FocusOut and obj is self._target):
                self.detach()
        return False

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        half = RING_WIDTH / 2.0
        r = QRectF(self.rect()).adjusted(half, half, -half, -half)
        p.setPen(QPen(QColor(theme.palette().accent), RING_WIDTH))
        p.setBrush(Qt.BrushStyle.NoBrush)
        rad = self._radius + RING_OFFSET + half if self._radius > 0 else 0
        p.drawPath(rounded_path(r, rad))
        p.end()


_ring: _FocusRing | None = None


def _ring_widget() -> _FocusRing:
    global _ring
    try:
        if _ring is not None:
            _ring.objectName()  # raises RuntimeError if the C++ object is gone
            return _ring
    except RuntimeError:
        pass
    _ring = _FocusRing()
    return _ring


def show_focus_ring(target: QWidget, radius: float = 8.0, sub_rect: QRect | None = None) -> None:
    """Show the keyboard focus ring around ``target`` (or ``sub_rect`` in target coordinates)."""
    _ring_widget().attach(target, radius, sub_rect)


def hide_focus_ring(target: QWidget | None = None) -> None:
    """Hide the focus ring (only if it belongs to ``target`` when given)."""
    if _ring is None:
        return
    try:
        _ring.detach(target)
    except RuntimeError:
        pass


class KeyboardFocusMixin:
    """Mixin for custom widgets: shows the shared focus ring only for keyboard focus.

    Subclasses may set ``_ring_radius`` and override :meth:`focus_ring_rect`.
    """

    _ring_radius: float = 8.0
    _kbd_focus: bool = False

    def focus_ring_rect(self) -> QRect | None:
        """Sub-rect (widget coordinates) the ring should surround; None = whole widget."""
        return None

    def update_focus_ring(self) -> None:
        """Re-position the ring (e.g. after keyboard navigation inside the widget)."""
        if self._kbd_focus and self.hasFocus():  # type: ignore[attr-defined]
            show_focus_ring(self, self._ring_radius, self.focus_ring_rect())  # type: ignore[arg-type]

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._kbd_focus = is_keyboard_reason(event.reason())
        super().focusInEvent(event)  # type: ignore[misc]
        if self._kbd_focus:
            show_focus_ring(self, self._ring_radius, self.focus_ring_rect())  # type: ignore[arg-type]
        self.update()  # type: ignore[attr-defined]

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._kbd_focus = False
        hide_focus_ring(self)  # type: ignore[arg-type]
        super().focusOutEvent(event)  # type: ignore[misc]
        self.update()  # type: ignore[attr-defined]


def connect_theme(widget: QWidget, slot: Callable[[], None] | None = None) -> None:
    """Repaint ``widget`` (or call ``slot``) when the current theme changes."""
    tm = theme.current()
    tm.changed.connect(slot or widget.update)

