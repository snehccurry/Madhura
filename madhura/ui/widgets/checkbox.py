"""Checkbox: custom-painted square or round check with a self-drawing tick and a pulse."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from madhura.ui import animations, fonts, theme
from madhura.ui.widgets._common import HoverFader, KeyboardFocusMixin, fill_rounded

__all__ = ["Checkbox"]


class Checkbox(KeyboardFocusMixin, QAbstractButton):
    """Checkable box (``round=False``) or circle (``round=True``) with optional text.

    ``box`` is the glyph diameter (default 18 square / 20 round; habits use 28). The hit area is
    at least 32 px tall. Checking draws the tick (180 ms) and pulses 1 → 1.1 → 1 (200 ms).
    ``dashed=True`` paints a dashed outline while unchecked (e.g. "skipped").
    """

    def __init__(
        self,
        round: bool = False,  # noqa: A002 - public API name from the spec
        text: str = "",
        parent: QWidget | None = None,
        *,
        box: int | None = None,
        checked: bool = False,
        accessible_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._round = round
        self._box = box or (20 if round else 18)
        self._dashed = False
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(text)
        self._hover = HoverFader(self, 120)
        self._fill = HoverFader(self, 160, 1.0 if checked else 0.0)
        self._tick = 1.0 if checked else 0.0
        self._scale = 1.0
        self._tick_anim = QVariantAnimation(self)
        self._tick_anim.setDuration(180)
        self._tick_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._tick_anim.valueChanged.connect(self._on_tick)
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(200)
        self._pulse.setKeyValueAt(0.0, 1.0)
        self._pulse.setKeyValueAt(0.45, 1.1)
        self._pulse.setKeyValueAt(1.0, 1.0)
        self._pulse.valueChanged.connect(self._on_pulse)
        self.toggled.connect(self._on_toggled)
        self.setFont(fonts.font(14, 400))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._ring_radius = self._box / 2.0 if round else 6.0
        self.setAccessibleName(accessible_name or text or "Done")

    # ------------------------------------------------------------------ API
    def set_dashed(self, dashed: bool) -> None:
        """Dashed outline while unchecked (skip state)."""
        self._dashed = bool(dashed)
        self.update()

    def is_round(self) -> bool:
        """True for the circular variant."""
        return self._round

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.updateGeometry()

    # ------------------------------------------------------------------ geometry
    def _glyph_rect(self) -> QRectF:
        b = self._box
        return QRectF(1, (self.height() - b) / 2.0, b, b)

    def focus_ring_rect(self) -> object:
        r = self._glyph_rect().toRect()
        return r

    def sizeHint(self) -> QSize:  # noqa: N802
        b = self._box
        w = b + 2
        if self.text():
            w += 10 + QFontMetrics(self.font()).horizontalAdvance(self.text())
        return QSize(max(w, 32 if not self.text() else w), max(32, b + 4))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, pos: object) -> bool:  # noqa: N802
        return self.rect().contains(pos)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ animation
    def _on_tick(self, v: object) -> None:
        self._tick = float(v)  # type: ignore[arg-type]
        self.update()

    def _on_pulse(self, v: object) -> None:
        self._scale = float(v)  # type: ignore[arg-type]
        self.update()

    def _on_toggled(self, on: bool) -> None:
        animate = self.isVisible() and animations.motion_enabled()
        self._fill.set_target(1.0 if on else 0.0, animate=animate)
        self._tick_anim.stop()
        self._pulse.stop()
        if on and animate:
            self._tick_anim.setStartValue(0.0)
            self._tick_anim.setEndValue(1.0)
            self._tick_anim.start()
            self._pulse.start()
        else:
            self._tick = 1.0 if on else 0.0
            self._scale = 1.0
            self.update()

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        g = self._glyph_rect()
        c = g.center()
        b = self._box * self._scale
        g = QRectF(c.x() - b / 2.0, c.y() - b / 2.0, b, b)
        radius = b / 2.0 if self._round else max(4.0, b * 0.26)
        f = self._fill.value
        h = self._hover.value
        border = theme.mix(pal.border_strong, pal.text_subtle, 0.3 + 0.7 * h)
        if f < 1.0:
            if self._dashed and not self.isChecked():
                pen = QPen(border, 1.5)
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setDashPattern([2.2, 2.2])
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                inset = g.adjusted(0.75, 0.75, -0.75, -0.75)
                if self._round:
                    p.drawEllipse(inset)
                else:
                    p.drawRoundedRect(inset, radius, radius)
            else:
                fill_rounded(p, g, radius, theme.with_alpha(pal.hover, 0.6 * h), border, 1.5)
        if f > 0.0:
            p.save()
            p.setOpacity(p.opacity() * f)
            fill_rounded(p, g, radius, theme.mix(pal.accent, pal.accent_hover, h))
            p.restore()
        if self._tick > 0.0 and self.isChecked():
            s = b / 20.0
            path = QPainterPath(QPointF(g.left() + 5.6 * s, g.top() + 10.4 * s))
            path.lineTo(QPointF(g.left() + 8.6 * s, g.top() + 13.3 * s))
            path.lineTo(QPointF(g.left() + 14.6 * s, g.top() + 7.0 * s))
            length = path.length()
            pen = QPen(QColor(pal.accent_text), max(1.6, 1.9 * s))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if self._tick < 1.0 and length > 0:
                width = pen.widthF()
                pen.setDashPattern([length * self._tick / width, length / width + 1])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if self.text():
            p.setFont(self.font())
            p.setPen(QColor(pal.text))
            x = self._glyph_rect().right() + 10
            p.drawText(QRectF(x, 0, self.width() - x, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())
        p.end()
