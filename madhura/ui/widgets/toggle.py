"""Toggle: an animated on/off switch (a checkable ``QAbstractButton``; use ``toggled``)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from madhura.ui import theme
from madhura.ui.widgets._common import HoverFader, KeyboardFocusMixin, fill_rounded

__all__ = ["Toggle"]

_TRACK_W = 36
_TRACK_H = 20
_KNOB = 16


class Toggle(KeyboardFocusMixin, QAbstractButton):
    """36×20 switch with a sliding knob (160 ms). Emits Qt's ``toggled(bool)``; ``clicked``
    fires only for user interaction."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None, *, accessible_name: str = "") -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._pos = HoverFader(self, 160, 1.0 if checked else 0.0)
        self._hover = HoverFader(self, 120)
        self.toggled.connect(lambda on: self._pos.set_target(1.0 if on else 0.0, animate=self.isVisible()))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(_TRACK_W, _TRACK_H)
        self._ring_radius = _TRACK_H / 2.0
        if accessible_name:
            self.setAccessibleName(accessible_name)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(_TRACK_W, _TRACK_H)

    def hitButton(self, pos: object) -> bool:  # noqa: N802
        return self.rect().contains(pos)  # type: ignore[arg-type]

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        t = self._pos.value
        h = self._hover.value
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            self._paint_disabled(p, pal, t)
            p.end()
            return
        off = theme.mix(pal.track, pal.text_subtle, 0.25 * h)
        on = theme.mix(pal.accent, pal.accent_hover, h)
        track = theme.mix(off, on, t)
        r = QRectF(0, 0, _TRACK_W, _TRACK_H)
        fill_rounded(p, r, _TRACK_H / 2.0, track)
        x = 2 + t * (_TRACK_W - _KNOB - 4)
        knob = QRectF(x, (_TRACK_H - _KNOB) / 2.0, _KNOB, _KNOB)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40 if pal.is_dark else 30))
        p.drawEllipse(knob.translated(0, 0.75))
        knob_color = QColor("#FFFFFF") if (t > 0.5 or not pal.is_dark) else theme.mix(pal.text_muted, pal.text, 0.5)
        p.setBrush(knob_color)
        p.drawEllipse(knob)
        p.end()

    @staticmethod
    def _paint_disabled(p: QPainter, pal: theme.Palette, t: float) -> None:
        """Disabled: flat track with a visible hairline outline and a muted knob (no opacity
        wash, which made the switch vanish on light surfaces)."""
        accent_soft = theme.mix(pal.surface, pal.accent, 0.4)
        track = theme.mix(pal.surface_alt, accent_soft, t)
        border = theme.mix(pal.border_strong, accent_soft, t)
        fill_rounded(p, QRectF(0, 0, _TRACK_W, _TRACK_H), _TRACK_H / 2.0, track, border)
        x = 2 + t * (_TRACK_W - _KNOB - 4)
        knob = QRectF(x, (_TRACK_H - _KNOB) / 2.0, _KNOB, _KNOB).adjusted(1, 1, -1, -1)
        off_knob = theme.mix(pal.surface_alt, pal.text_subtle, 0.55)
        on_knob = theme.mix(pal.surface, QColor("#FFFFFF"), 0.75)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.mix(off_knob, on_knob, t))
        p.drawEllipse(knob)
