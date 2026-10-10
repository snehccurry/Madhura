"""Selection chips: :class:`Chip`, :class:`ChipGroup`, :class:`SegmentedControl`, :class:`DayChips`.

Groups are painted by a single widget: one tab stop, arrow keys move between items, Space/Enter
activates. Selected items use ``accent_soft`` fill + 1 px ``accent@55%`` border + ``text`` label.
Programmatic setters (``set_value`` …) do NOT emit change signals; user interaction does.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QToolTip, QWidget

from madhura.ui import animations, fonts, icons, theme
from madhura.ui.widgets._common import HoverFader, KeyboardFocusMixin, fill_rounded

__all__ = ["Chip", "ChipGroup", "SegmentedControl", "DayChips", "normalize_options"]

#: size → (height, horizontal padding, font px)
_CHIP_SIZES = {"sm": (28, 10, 12), "md": (32, 12, 13)}


def normalize_options(options: Iterable[Any]) -> list[tuple[Any, str]]:
    """Accept ``[(value, label), …]`` or ``["label", …]`` and return ``[(value, label), …]``."""
    out: list[tuple[Any, str]] = []
    for opt in options:
        if isinstance(opt, (tuple, list)) and len(opt) == 2:
            out.append((opt[0], str(opt[1])))
        else:
            out.append((opt, str(opt)))
    return out


def _chip_colors(selected: bool, hover: float, enabled: bool = True) -> tuple[QColor, QColor | None, QColor]:
    """(fill, border, text) for a chip."""
    p = theme.palette()
    if selected:
        fill = theme.mix(p.accent_soft, theme.with_alpha(p.accent, 0.22), hover)
        return fill, QColor(p.accent_border), QColor(p.text)
    fill = theme.mix(p.surface_alt, p.hover, hover)
    text = theme.mix(p.text_muted, p.text, hover)
    return fill, QColor(p.border_strong if p.is_dark else p.border_strong), text


class Chip(KeyboardFocusMixin, QAbstractButton):
    """A single pill chip (checkable by default). Use for reason/suggestion chips."""

    def __init__(
        self,
        text: str = "",
        checkable: bool = True,
        icon: str | None = None,
        parent: QWidget | None = None,
        *,
        size: str = "md",
    ) -> None:
        super().__init__(parent)
        self._size = size if size in _CHIP_SIZES else "md"
        self._icon = icon
        self._hover = HoverFader(self, 120)
        self.setText(text)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFont(fonts.font(_CHIP_SIZES[self._size][2], 500))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        h = _CHIP_SIZES[self._size][0]
        self._ring_radius = h / 2.0
        self.setAccessibleName(text)

    def set_icon_name(self, name: str | None) -> None:
        """Set/clear a leading 14 px icon."""
        self._icon = name
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        h, pad, _f = _CHIP_SIZES[self._size]
        w = QFontMetrics(self.font()).horizontalAdvance(self.text()) + 2 * pad
        if self._icon:
            w += 14 + 6
        return QSize(max(w, h), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        hover = 1.0 if self.isDown() else self._hover.value
        fill, border, text = _chip_colors(self.isChecked(), hover)
        r = QRectF(self.rect())
        fill_rounded(p, r, r.height() / 2.0, fill, border)
        _h, pad, _f = _CHIP_SIZES[self._size]
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self.text())
        total = tw + ((14 + 6) if self._icon else 0)
        x = (r.width() - total) / 2.0
        if self._icon:
            pm = icons.pixmap(self._icon, 14, text, self.devicePixelRatioF())
            p.drawPixmap(int(x), int((r.height() - 14) / 2), pm)
            x += 20
        p.setPen(text)
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, tw + 2, r.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())
        p.end()


class ChipGroup(KeyboardFocusMixin, QWidget):
    """A row (wrapping) of painted chips with exclusive or multi selection.

    Signals: ``selectionChanged(object)`` — the new value (exclusive) or list of values (multi);
    ``chipClicked(object)`` — the clicked value on every activation (even if already selected).
    """

    selectionChanged = Signal(object)
    chipClicked = Signal(object)

    def __init__(
        self,
        options: Iterable[Any] = (),
        exclusive: bool = True,
        value: Any = None,
        parent: QWidget | None = None,
        *,
        size: str = "md",
        allow_empty: bool = False,
        wrap: bool = True,
        spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        self._exclusive = exclusive
        self._allow_empty = allow_empty
        self._wrap = wrap
        self._spacing = spacing
        self._size = size if size in _CHIP_SIZES else "md"
        self._options: list[tuple[Any, str]] = []
        self._selected: list[Any] = []
        self._hover_index = -1
        self._press_index = -1
        self._focus_index = 0
        self.setFont(fonts.font(_CHIP_SIZES[self._size][2], 500))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred if wrap else QSizePolicy.Policy.Fixed)
        sp.setHeightForWidth(wrap)
        self.setSizePolicy(sp)
        self._ring_radius = _CHIP_SIZES[self._size][0] / 2.0
        self.set_options(options)
        if value is not None:
            if exclusive:
                self.set_value(value)
            else:
                self.set_values(list(value))

    # ------------------------------------------------------------------ options / values
    def options(self) -> list[tuple[Any, str]]:
        """Current ``(value, label)`` list."""
        return list(self._options)

    def set_options(self, options: Iterable[Any]) -> None:
        """Replace the options (selection is kept where values still exist)."""
        self._options = normalize_options(options)
        values = [v for v, _ in self._options]
        self._selected = [v for v in self._selected if v in values]
        self._focus_index = min(self._focus_index, max(0, len(self._options) - 1))
        self.updateGeometry()
        self.update()

    def set_label(self, value: Any, label: str) -> None:
        """Relabel one option (e.g. "Custom" → "35m")."""
        self._options = [(v, label if v == value else lab) for v, lab in self._options]
        self.updateGeometry()
        self.update()

    def label_for(self, value: Any) -> str | None:
        """Label of ``value`` (None if absent)."""
        for v, lab in self._options:
            if v == value:
                return lab
        return None

    def value(self) -> Any:
        """Selected value (exclusive mode) or None."""
        return self._selected[0] if self._selected else None

    def values(self) -> list[Any]:
        """Selected values in option order."""
        return [v for v, _ in self._options if v in self._selected]

    def set_value(self, value: Any) -> None:
        """Select ``value`` (None clears). Does not emit."""
        values = [v for v, _ in self._options]
        self._selected = [value] if value is not None and value in values else []
        if self._selected:
            self._focus_index = values.index(value)
        self._sync_accessible()
        self.update()

    def set_values(self, values: Sequence[Any]) -> None:
        """Select several values (multi mode). Does not emit."""
        valid = [v for v, _ in self._options]
        self._selected = [v for v in values if v in valid]
        self._sync_accessible()
        self.update()

    def _sync_accessible(self) -> None:
        labels = [lab for v, lab in self._options if v in self._selected]
        self.setAccessibleDescription(", ".join(labels) if labels else "None selected")

    def is_exclusive(self) -> bool:
        """True for single-selection groups."""
        return self._exclusive

    # ------------------------------------------------------------------ geometry
    def _chip_width(self, label: str) -> int:
        _h, pad, _f = _CHIP_SIZES[self._size]
        return QFontMetrics(self.font()).horizontalAdvance(label) + 2 * pad

    def _layout_rects(self, width: int) -> list[QRect]:
        h = _CHIP_SIZES[self._size][0]
        rects: list[QRect] = []
        x = y = 0
        for _v, label in self._options:
            w = max(h, self._chip_width(label))
            if self._wrap and x > 0 and x + w > width:
                x = 0
                y += h + self._spacing
            rects.append(QRect(x, y, w, h))
            x += w + self._spacing
        return rects

    def chip_rect(self, value: Any) -> QRect:
        """Rectangle (widget coordinates) of the chip for ``value`` (null rect if absent)."""
        rects = self._layout_rects(self.width() or self.sizeHint().width())
        for (v, _lab), r in zip(self._options, rects):
            if v == value:
                return r
        return QRect()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return self._wrap

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rects = self._layout_rects(width)
        return (max(r.bottom() for r in rects) + 1) if rects else _CHIP_SIZES[self._size][0]

    def _single_row_width(self) -> int:
        h = _CHIP_SIZES[self._size][0]
        w = sum(max(h, self._chip_width(lab)) for _v, lab in self._options)
        return max(h, w + self._spacing * max(0, len(self._options) - 1))

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self._single_row_width()
        if self._wrap and 0 < self.width() < w:
            return QSize(w, self.heightForWidth(self.width()))
        return QSize(w, _CHIP_SIZES[self._size][0])

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        h = _CHIP_SIZES[self._size][0]
        if not self._wrap:
            return self.sizeHint()
        widest = max((self._chip_width(lab) for _v, lab in self._options), default=h)
        return QSize(widest, h)

    def _index_at(self, pos: QPoint) -> int:
        for i, r in enumerate(self._layout_rects(self.width())):
            if r.contains(pos):
                return i
        return -1

    def focus_ring_rect(self) -> QRect | None:
        rects = self._layout_rects(self.width())
        if 0 <= self._focus_index < len(rects):
            return rects[self._focus_index]
        return None

    # ------------------------------------------------------------------ interaction
    def _activate(self, index: int) -> None:
        if not (0 <= index < len(self._options)):
            return
        value = self._options[index][0]
        self._focus_index = index
        changed = False
        if self._exclusive:
            if value in self._selected:
                if self._allow_empty:
                    self._selected = []
                    changed = True
            else:
                self._selected = [value]
                changed = True
        else:
            if value in self._selected:
                self._selected.remove(value)
            else:
                self._selected.append(value)
            changed = True
        self.update()
        self.update_focus_ring()
        self._sync_accessible()
        self.chipClicked.emit(value)
        if changed:
            self.selectionChanged.emit(self.value() if self._exclusive else self.values())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position().toPoint())
        if idx != self._hover_index:
            self._hover_index = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_index = self._index_at(event.position().toPoint())
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._index_at(event.position().toPoint())
            pressed = self._press_index
            self._press_index = -1
            if idx >= 0 and idx == pressed and self.isEnabled():
                self._activate(idx)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        n = len(self._options)
        if n == 0:
            super().keyPressEvent(event)
            return
        key = event.key()
        step = 0
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            step = 1
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            step = -1
        if step:
            self._focus_index = (self._focus_index + step) % n
            if self._exclusive:
                self._activate(self._focus_index)
            else:
                self.update_focus_ring()
                self.update()
            event.accept()
            return
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self._focus_index = 0 if key == Qt.Key.Key_Home else n - 1
            if self._exclusive:
                self._activate(self._focus_index)
            self.update_focus_ring()
            event.accept()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._activate(self._focus_index)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: Any) -> None:  # noqa: N802
        if self._exclusive and self._selected:
            values = [v for v, _ in self._options]
            if self._selected[0] in values:
                self._focus_index = values.index(self._selected[0])
        super().focusInEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._wrap and event.oldSize().width() != event.size().width():
            self.updateGeometry()
        self.update_focus_ring()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.setFont(self.font())
        for i, ((value, label), r) in enumerate(zip(self._options, self._layout_rects(self.width()))):
            hover = 1.0 if i == self._press_index else (0.6 if i == self._hover_index else 0.0)
            fill, border, text = _chip_colors(value in self._selected, hover)
            rf = QRectF(r)
            fill_rounded(p, rf, rf.height() / 2.0, fill, border)
            p.setPen(text)
            p.drawText(rf, int(Qt.AlignmentFlag.AlignCenter), label)
        p.end()


class SegmentedControl(KeyboardFocusMixin, QWidget):
    """Segmented picker (e.g. "7 days | 30 days | 12 weeks") with an animated selection pill.

    Emits ``valueChanged(object)`` on user changes only.
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        options: Iterable[Any] = (),
        value: Any = None,
        parent: QWidget | None = None,
        *,
        size: str = "md",
        equal_width: bool = False,
    ) -> None:
        super().__init__(parent)
        self._options: list[tuple[Any, str]] = []
        self._index = -1
        self._hover_index = -1
        self._equal = equal_width
        self._height = 32 if size != "sm" else 28
        self._pad = 14 if size != "sm" else 10
        self._indicator: QRectF | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setFont(fonts.font(13 if size != "sm" else 12, 500))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._ring_radius = 8.0
        self.set_options(options)
        if value is not None:
            self.set_value(value)

    def options(self) -> list[tuple[Any, str]]:
        """Current ``(value, label)`` list."""
        return list(self._options)

    def set_options(self, options: Iterable[Any]) -> None:
        """Replace the options (keeps the selection when the value still exists)."""
        old = self.value()
        self._options = normalize_options(options)
        values = [v for v, _ in self._options]
        self._index = values.index(old) if old in values else (-1 if not values else self._index if 0 <= self._index < len(values) else -1)
        self._indicator = None
        self.setFixedSize(self.sizeHint())
        self.update()

    def value(self) -> Any:
        """Selected value (None if nothing selected)."""
        if 0 <= self._index < len(self._options):
            return self._options[self._index][0]
        return None

    def set_value(self, value: Any) -> None:
        """Select ``value`` without emitting."""
        values = [v for v, _ in self._options]
        if value in values:
            self._set_index(values.index(value), emit=False)

    def _segment_widths(self) -> list[int]:
        fm = QFontMetrics(self.font())
        widths = [fm.horizontalAdvance(lab) + 2 * self._pad for _v, lab in self._options]
        if self._equal and widths:
            m = max(widths)
            widths = [m] * len(widths)
        return widths

    def _segment_rects(self) -> list[QRectF]:
        rects: list[QRectF] = []
        x = 2.0
        for w in self._segment_widths():
            rects.append(QRectF(x, 2.0, w, self._height - 4.0))
            x += w
        return rects

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(sum(self._segment_widths()) + 4, self._height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def _on_anim(self, v: object) -> None:
        self._indicator = v  # type: ignore[assignment]
        self.update()

    def _set_index(self, index: int, emit: bool) -> None:
        if index == self._index or not (0 <= index < len(self._options)):
            return
        rects = self._segment_rects()
        target = rects[index]
        start = self._indicator if self._indicator is not None else (rects[self._index] if 0 <= self._index < len(rects) else None)
        self._index = index
        self._anim.stop()
        if start is None or not animations.motion_enabled() or not self.isVisible():
            self._indicator = target
            self.update()
        else:
            self._anim.setStartValue(QRectF(start))
            self._anim.setEndValue(QRectF(target))
            self._anim.start()
        if emit:
            self.valueChanged.emit(self.value())

    def _index_at(self, pos: QPoint) -> int:
        for i, r in enumerate(self._segment_rects()):
            if r.adjusted(-2, -2, 2, 2).contains(pos.x(), pos.y()):
                return i
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position().toPoint())
        if idx != self._hover_index:
            self._hover_index = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            idx = self._index_at(event.position().toPoint())
            if idx >= 0:
                self._set_index(idx, emit=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        n = len(self._options)
        key = event.key()
        if n and key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._set_index(max(0, self._index - 1) if self._index >= 0 else 0, emit=True)
        elif n and key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._set_index(min(n - 1, self._index + 1), emit=True)
        elif n and key == Qt.Key.Key_Home:
            self._set_index(0, emit=True)
        elif n and key == Qt.Key.Key_End:
            self._set_index(n - 1, emit=True)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        outer = QRectF(self.rect())
        fill_rounded(p, outer, 8.0, pal.surface_alt, pal.border)
        rects = self._segment_rects()
        if 0 <= self._index < len(rects):
            ind = self._indicator if self._indicator is not None else rects[self._index]
            fill_rounded(p, ind, 6.0, pal.accent_soft, pal.accent_border)
        p.setFont(self.font())
        for i, ((_v, label), r) in enumerate(zip(self._options, rects)):
            if i == self._index:
                c = QColor(pal.text)
            elif i == self._hover_index:
                c = QColor(pal.text)
            else:
                c = QColor(pal.text_muted)
            p.setPen(c)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), label)
        p.end()


class DayChips(KeyboardFocusMixin, QWidget):
    """Seven round weekday toggles (Mon … Sun). Mask bit 0 = Monday … bit 6 = Sunday.

    Emits ``maskChanged(int)`` on user changes only.
    """

    maskChanged = Signal(int)

    LETTERS = ("M", "T", "W", "T", "F", "S", "S")
    NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    def __init__(self, mask: int = 127, parent: QWidget | None = None, *, diameter: int = 32, gap: int = 6) -> None:
        super().__init__(parent)
        self._mask = int(mask) & 127
        self._d = diameter
        self._gap = gap
        self._hover = -1
        self._focus = 0
        self.setFont(fonts.font(12, 600))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedSize(7 * diameter + 6 * gap, diameter)
        self._ring_radius = diameter / 2.0
        self.setAccessibleName("Days of the week")
        self._update_description()

    def mask(self) -> int:
        """Current bitmask."""
        return self._mask

    def set_mask(self, mask: int) -> None:
        """Set the mask without emitting."""
        self._mask = int(mask) & 127
        self._update_description()
        self.update()

    def is_day_set(self, day: int) -> bool:
        """True if weekday ``day`` (0 = Monday) is selected."""
        return bool(self._mask & (1 << day))

    def _update_description(self) -> None:
        days = [self.NAMES[i] for i in range(7) if self._mask & (1 << i)]
        self.setAccessibleDescription(", ".join(days) if days else "No days")

    def _rect(self, i: int) -> QRect:
        return QRect(i * (self._d + self._gap), 0, self._d, self._d)

    def focus_ring_rect(self) -> QRect | None:
        return self._rect(self._focus)

    def _index_at(self, pos: QPoint) -> int:
        for i in range(7):
            if self._rect(i).adjusted(-self._gap // 2, 0, self._gap // 2, 0).contains(pos):
                return i
        return -1

    def _toggle(self, i: int) -> None:
        self._mask ^= 1 << i
        self._focus = i
        self._update_description()
        self.update()
        self.update_focus_ring()
        self.maskChanged.emit(self._mask)

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            idx = self._index_at(e.pos())
            if idx >= 0:
                QToolTip.showText(e.globalPos(), self.NAMES[idx], self, self._rect(idx))
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            idx = self._index_at(event.position().toPoint())
            if idx >= 0:
                self._toggle(idx)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._focus = (self._focus - 1) % 7
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._focus = (self._focus + 1) % 7
        elif key == Qt.Key.Key_Home:
            self._focus = 0
        elif key == Qt.Key.Key_End:
            self._focus = 6
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._toggle(self._focus)
            event.accept()
            return
        else:
            super().keyPressEvent(event)
            return
        self.update_focus_ring()
        self.update()
        event.accept()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.setFont(self.font())
        for i in range(7):
            selected = bool(self._mask & (1 << i))
            fill, border, text = _chip_colors(selected, 0.6 if i == self._hover else 0.0)
            r = QRectF(self._rect(i))
            fill_rounded(p, r, r.height() / 2.0, fill, border)
            p.setPen(text)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self.LETTERS[i])
        p.end()
