"""ProgressRing: the focus timer ring (also used small in the pill, goal ring and sidebar)."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QHideEvent, QPainter, QPaintEvent, QPen, QPixmap, QShowEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from madhura.ui import animations, fonts, theme

__all__ = ["BREATH_FRAME_MS", "BREATH_MIN_OPACITY", "BREATH_PERIOD_MS", "ProgressRing"]

#: One breath (1 → 0.55 → 1 opacity), in ms.
BREATH_PERIOD_MS = 2400
BREATH_MIN_OPACITY = 0.55
#: ~15 fps is plenty for a 2.4 s fade (and a quarter of the repaints of a 60 fps animation).
BREATH_FRAME_MS = 66
_BREATH_STEP = 0.01  # smaller opacity changes are not worth a repaint


class ProgressRing(QWidget):
    """Circular progress arc starting at 12 o'clock and running clockwise.

    ``progress`` is the drawn fraction (0..1) — pass the *remaining* fraction for a depleting
    timer. Optional centre text (``set_text``) and caption (``set_caption``). Colours default to
    the accent (arc) and ``track`` tokens and follow theme changes. ``set_breathing(True)`` makes
    arc + text breathe 0.55 ↔ 1 over 2.4 s (skipped with reduce-motion). The breathing only
    animates while the ring is on screen: hidden (to the tray), minimized or with reduce-motion
    on it stops, and it picks up again when the ring is shown.
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
        self._breath_wanted = False
        self._on_screen = False  # between showEvent and hideEvent (minimize sends a hide too)
        self._breath_timer = QTimer(self)
        self._breath_timer.setInterval(BREATH_FRAME_MS)
        self._breath_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._breath_timer.timeout.connect(self._on_breath_frame)
        self._breath_clock = QElapsedTimer()
        #: While breathing: slot ("track" / "face") → (key, pixmap); see :meth:`_layer`.
        self._layers: dict[str, tuple[tuple[object, ...], QPixmap]] = {}
        theme.current().motion_changed.connect(self._on_motion_changed)
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
        self._breath_wanted = bool(enabled)
        self._sync_breath()

    def is_breathing(self) -> bool:
        """True while breathing is on (requested, motion allowed); it animates while on screen."""
        return self._breath_wanted and animations.motion_enabled()

    def is_breath_animating(self) -> bool:
        """True while breathing frames are being painted (on screen only)."""
        return self._breath_timer.isActive()

    def opacity(self) -> float:
        """Current opacity of arc + text (1.0 unless breathing)."""
        return self._opacity

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._d, self._d)

    # ------------------------------------------------------------------ internals
    def _on_anim(self, v: object) -> None:
        self._progress = float(v)  # type: ignore[arg-type]
        self.update()

    def _sync_breath(self) -> None:
        if self._breath_wanted and self._on_screen and animations.motion_enabled():
            if not self._breath_timer.isActive():
                self._breath_clock.start()  # every breath starts from full opacity
                self._breath_timer.start()
            return
        self._breath_timer.stop()
        self._layers.clear()
        if self._opacity != 1.0:
            self._opacity = 1.0
            self.update()

    def _on_motion_changed(self, _reduce: bool) -> None:
        self._sync_breath()

    def _on_breath_frame(self) -> None:
        if not animations.motion_enabled():
            self._sync_breath()
            return
        phase = (self._breath_clock.elapsed() % BREATH_PERIOD_MS) / BREATH_PERIOD_MS
        # Sine in-out down to the minimum and back up: 1 → 0.55 → 1.
        opacity = 1.0 - (1.0 - BREATH_MIN_OPACITY) * (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0
        if abs(opacity - self._opacity) >= _BREATH_STEP:
            self._opacity = opacity
            self.update()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._on_screen = True
        self._sync_breath()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        if self._anim.state() == QVariantAnimation.State.Running:
            end = float(self._anim.endValue())
            self._anim.stop()
            self._progress = end
        # Hidden to the tray, minimized (a spontaneous hide) or its page switched away: no frames
        # nobody can see — they would keep waking the CPU for hours while paused in the tray.
        self._on_screen = False
        self._sync_breath()
        super().hideEvent(event)

    def _layer(self, slot: str, key: tuple[object, ...], paint: Callable[[QPainter], None]) -> QPixmap:
        """A cached, transparent ring-sized layer drawn by *paint* (rebuilt when *key* changes).

        While breathing nothing but the opacity changes from frame to frame, so each frame only
        blits the track and the arc + text layers instead of re-rasterizing them (the
        antialiased track and the 60 px digits are what make a frame cost).
        """
        entry = self._layers.get(slot)
        if entry is not None and entry[0] == key:
            return entry[1]
        dpr = self.devicePixelRatioF()
        side = max(1, math.ceil(self._d * dpr))
        pm = QPixmap(side, side)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        paint(q)
        q.end()
        self._layers[slot] = (key, pm)
        return pm

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        track = QColor(self._track) if self._track is not None else QColor(pal.track)
        if self._breath_timer.isActive():
            dpr = self.devicePixelRatioF()
            base = (self._thickness, self._d, dpr)
            p.drawPixmap(0, 0, self._layer("track", (track.rgba(), *base), lambda q: self._paint_track(q, track)))
            colors = tuple(c.rgba() if c is not None else None for c in (self._color, self._text_color, self._caption_color))
            face_key = (
                *base, self._progress, self._text, self._caption, colors, pal.accent, pal.text, pal.text_muted,
                self._center_font.key(), self._caption_font.key(),
            )
            p.setOpacity(self._opacity)
            p.drawPixmap(0, 0, self._layer("face", face_key, lambda q: self._paint_face(q, pal)))
        else:
            self._layers.clear()
            self._paint_track(p, track)
            p.setOpacity(self._opacity)
            self._paint_face(p, pal)
        p.end()

    def _ring_rect(self) -> QRectF:
        half = self._thickness / 2.0 + 0.5
        return QRectF(half, half, self._d - 2 * half, self._d - 2 * half)

    def _paint_track(self, p: QPainter, track: QColor) -> None:
        pen = QPen(track, self._thickness)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(self._ring_rect())

    def _paint_face(self, p: QPainter, pal: theme.Palette) -> None:
        """The arc and the centre text / caption (everything that breathes)."""
        t = self._thickness
        r = self._ring_rect()
        p.setBrush(Qt.BrushStyle.NoBrush)
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
