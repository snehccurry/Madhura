"""ProgressRing: the focus timer ring (also used small in the pill, goal ring and sidebar)."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from madhura.ui import animations, fonts, theme

__all__ = ["ProgressRing"]


class ProgressRing(QWidget):
    """Circular progress arc starting at 12 o'clock and running clockwise.

    ``progress`` is the drawn fraction (0..1) — pass the *remaining* fraction for a depleting
    timer. Optional centre text (``set_text``) and caption (``set_caption``). Colours default to
    the accent (arc) and ``track`` tokens and follow theme changes. ``set_breathing(True)`` makes
    arc + text breathe 0.55 ↔ 1 over 2.4 s (skipped with reduce-motion).
    """

    def __init__(self, diameter: int = 280, thickness: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._d = int(diameter)
        self._thickness = float(thickness)
        self._progress = 0.0
        self._color: QColor | None = None
        self._track: QColor | None = None
        self._text_color: QColor | None = None
        self._caption_color: QColor | None = None
        self._text = ""
        self._caption = ""
        self._opacity = 1.0
        self._center_font: QFont = (
            fonts.display_font(60, 300) if diameter >= 240 else fonts.display_font(max(10, diameter * 0.22), 400)
        )
        self._caption_font: QFont = fonts.font(13 if diameter >= 160 else 11, 500)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._breath = QVariantAnimation(self)
        self._breath.setDuration(2400)
        self._breath.setKeyValueAt(0.0, 1.0)
        self._breath.setKeyValueAt(0.5, 0.55)
        self._breath.setKeyValueAt(1.0, 1.0)
        self._breath.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._breath.setLoopCount(-1)
        self._breath.valueChanged.connect(self._on_breath)
        self.setFixedSize(self._d, self._d)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Time remaining")

    # ------------------------------------------------------------------ API
    def diameter(self) -> int:
        """Widget diameter in px."""
        return self._d

    def progress(self) -> float:
        """Current (target) progress 0..1."""
        if self._anim.state() == QVariantAnimation.State.Running:
            return float(self._anim.endValue())
        return self._progress

    def displayed_progress(self) -> float:
        """Progress currently painted (differs from :meth:`progress` mid-tween)."""
        return self._progress

    def set_progress(self, value: float, animate: bool = False, duration: int = 400) -> None:
        """Set the arc fraction; ``animate`` tweens (400 ms OutCubic) — use for jumps only."""
        value = max(0.0, min(1.0, float(value)))
        if animate and animations.motion_enabled() and self.isVisible():
            self._anim.stop()
            self._anim.setDuration(int(duration))
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(value)
            self._anim.start()
            return
        if self._anim.state() == QVariantAnimation.State.Running:
            self._anim.stop()
        if abs(value - self._progress) < 1e-4:
            return
        self._progress = value
        self.update()

    def sweep_in(self, value: float, duration: int = 600) -> None:
        """Start-of-session sweep 0 → ``value``."""
        self._progress = 0.0
        self.set_progress(value, animate=True, duration=duration)

    def set_color(self, color: QColor | str | None) -> None:
        """Arc colour (None → accent)."""
        self._color = QColor(color) if color is not None else None
        self.update()

    def set_track_color(self, color: QColor | str | None) -> None:
        """Track colour (None → ``track`` token)."""
        self._track = QColor(color) if color is not None else None
        self.update()

    def set_text_color(self, color: QColor | str | None) -> None:
        """Centre text colour (None → ``text``)."""
        self._text_color = QColor(color) if color is not None else None
        self.update()

    def set_caption_color(self, color: QColor | str | None) -> None:
        """Caption colour (None → ``text_muted``)."""
        self._caption_color = QColor(color) if color is not None else None
        self.update()

    def set_text(self, main: str) -> None:
        """Centre text (e.g. "24:59")."""
        if main != self._text:
            self._text = main
            self.update()

    def text(self) -> str:
        """Centre text."""
        return self._text

    def set_caption(self, text: str) -> None:
        """Caption under the centre text (e.g. "of 60 min" / "Paused")."""
        if text != self._caption:
            self._caption = text
            self.update()

    def caption(self) -> str:
        """Caption text."""
        return self._caption

    def set_center_font(self, f: QFont) -> None:
        """Font for the centre text (use :func:`fonts.display_font`)."""
        self._center_font = QFont(f)
        self.update()

    def set_caption_font(self, f: QFont) -> None:
        """Font for the caption."""
        self._caption_font = QFont(f)
        self.update()

    def set_thickness(self, thickness: float) -> None:
        """Stroke width of track and arc."""
        self._thickness = float(thickness)
        self.update()

    def set_breathing(self, enabled: bool) -> None:
        """Slow opacity breathing for the paused state (no-op with reduce-motion)."""
        if enabled and animations.motion_enabled():
            if self._breath.state() != QVariantAnimation.State.Running:
                self._breath.start()
        else:
            self._breath.stop()
            if self._opacity != 1.0:
                self._opacity = 1.0
                self.update()

    def is_breathing(self) -> bool:
        """True while the breathing animation runs."""
        return self._breath.state() == QVariantAnimation.State.Running

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._d, self._d)

    # ------------------------------------------------------------------ internals
    def _on_anim(self, v: object) -> None:
        self._progress = float(v)  # type: ignore[arg-type]
        self.update()

    def _on_breath(self, v: object) -> None:
        self._opacity = float(v)  # type: ignore[arg-type]
        self.update()

    def hideEvent(self, event: object) -> None:  # noqa: N802
        if self._anim.state() == QVariantAnimation.State.Running:
            end = float(self._anim.endValue())
            self._anim.stop()
            self._progress = end
        super().hideEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        t = self._thickness
        half = t / 2.0 + 0.5
        r = QRectF(half, half, self._d - 2 * half, self._d - 2 * half)
        track = QColor(self._track) if self._track is not None else QColor(pal.track)
        pen = QPen(track, t)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.setOpacity(self._opacity)
        if self._progress > 0.0005:
            arc = QColor(self._color) if self._color is not None else QColor(pal.accent)
            pen = QPen(arc, t)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            span = -int(round(self._progress * 360 * 16))
            if self._progress >= 0.9995:
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                p.setPen(pen)
                p.drawEllipse(r)
            else:
                p.drawArc(r, 90 * 16, span)
        if self._text:
            p.setFont(self._center_font)
            fm = QFontMetrics(self._center_font)
            tc = QColor(self._text_color) if self._text_color is not None else QColor(pal.text)
            text_h = fm.capHeight()
            cfm = QFontMetrics(self._caption_font)
            cap_gap = max(8.0, self._d * 0.05)
            cap_block = (cap_gap + cfm.capHeight()) if self._caption else 0.0
            top = (self._d - (text_h + cap_block)) / 2.0
            baseline = top + text_h
            tw = fm.horizontalAdvance(self._text)
            p.setPen(tc)
            p.drawText(QPointF((self._d - tw) / 2.0, baseline), self._text)
            if self._caption:
                p.setFont(self._caption_font)
                cc = QColor(self._caption_color) if self._caption_color is not None else QColor(pal.text_muted)
                p.setPen(cc)
                cw = cfm.horizontalAdvance(self._caption)
                p.drawText(QPointF((self._d - cw) / 2.0, baseline + cap_gap + cfm.capHeight()), self._caption)
        elif self._caption:
            p.setFont(self._caption_font)
            p.setPen(QColor(self._caption_color) if self._caption_color is not None else QColor(pal.text_muted))
            p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self._caption)
        p.end()
