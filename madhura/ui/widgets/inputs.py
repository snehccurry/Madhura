"""Inputs: :class:`TextField`, :class:`TextArea`, :class:`TimeField`, :class:`Stepper`,
:class:`Slider` and the app-wide wheel guard.

Text-like inputs share one look: ``input`` fill, 1 px ``border_strong`` (2 px accent when focused,
danger when in error), radius 8. The border area is always 2 px so focus never shifts content.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFocusEvent, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from madhura.ui import fonts, icons, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import IconButton

__all__ = [
    "FieldFrame",
    "TextField",
    "TextArea",
    "TimeField",
    "Stepper",
    "Slider",
    "WheelGuard",
    "install_wheel_guard",
    "style_completer",
]


def style_completer(completer: QCompleter) -> QCompleter:
    """Make a QCompleter popup match the theme (styled delegate, no frame artefacts)."""
    popup = completer.popup()
    popup.setItemDelegate(QStyledItemDelegate(popup))
    popup.setFont(fonts.font(13))
    return completer


class FieldFrame(QWidget):
    """Painted input chrome (fill + border + optional leading icon/dot) around an editor.

    The frame tracks focus/hover of ``editor`` and repaints itself; content margins are fixed so
    the 1 px → 2 px focus border never shifts text.
    """

    def __init__(self, height: int = 40, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor: QWidget | None = None
        self._icon: str | None = None
        self._dot: QColor | None = None
        self._error = False
        self._hover = False
        self._radius = 10.0 if height >= 48 else 8.0
        self._icon_size = 18 if height >= 48 else 16
        self.setFixedHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setSpacing(8)
        self._update_margins()

    def set_editor(self, editor: QWidget) -> None:
        """Insert ``editor`` (takes the stretch) and track its focus."""
        self._editor = editor
        editor.installEventFilter(self)
        self.layout_.insertWidget(0, editor, 1)

    def set_icon(self, name: str | None) -> None:
        """Leading icon (replaces any dot)."""
        if name is not None and not icons.has(name):
            raise KeyError(name)
        self._icon = name
        if name:
            self._dot = None
        self._update_margins()
        self.update()

    def set_dot(self, color: QColor | str | None) -> None:
        """Leading 8 px colour dot (replaces any icon); None removes it."""
        self._dot = QColor(color) if color is not None else None
        if self._dot is not None:
            self._icon = None
        self._update_margins()
        self.update()

    def set_error(self, error: bool) -> None:
        """Error border on/off."""
        self._error = bool(error)
        self.update()

    def _lead_width(self) -> int:
        if self._icon:
            return self._icon_size + 8
        if self._dot is not None:
            return 8 + 8
        return 0

    def _update_margins(self) -> None:
        pad = 14 if self.height() >= 48 else 12
        self.layout_.setContentsMargins(pad + self._lead_width(), 2, 8, 2)

    def is_focused(self) -> bool:
        """True while the editor (or a child of the frame) has focus."""
        fw = QApplication.focusWidget()
        return fw is not None and (fw is self or self.isAncestorOf(fw))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._editor and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.EnabledChange):
            self.update()
        return False

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._editor is not None and self._editor.isEnabled():
            self._editor.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        focused = enabled and self.is_focused()
        r = QRectF(self.rect())
        fill = pal.input if enabled else pal.surface_alt
        if self._error:
            border, width = QColor(pal.danger), (2.0 if focused else 1.0)
        elif focused:
            border, width = QColor(pal.accent), 2.0
        elif self._hover and enabled:
            border, width = theme.mix(pal.border_strong, pal.text_subtle, 0.6), 1.0
        else:
            border, width = QColor(pal.border_strong if enabled else pal.border), 1.0
        fill_rounded(p, r, self._radius, fill, border, width)
        pad = 14 if self.height() >= 48 else 12
        if self._icon:
            s = self._icon_size
            col = pal.accent if focused and not self._error else pal.text_muted
            if not enabled:
                col = pal.text_subtle
            pm = icons.pixmap(self._icon, s, col, self.devicePixelRatioF())
            p.drawPixmap(pad, int((self.height() - s) / 2), pm)
        elif self._dot is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._dot)
            p.drawEllipse(QRectF(pad, (self.height() - 8) / 2.0, 8, 8))
        p.end()

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._update_margins()


class TextField(QWidget):
    """Single-line text input with optional leading icon/dot, trailing widget and error text.

    ``set_error(text)`` shows a danger border + helper text below; typing clears it.
    Signals mirror QLineEdit: ``returnPressed``, ``textChanged(str)``, ``textEdited(str)``,
    ``editingFinished``. Focus is proxied to :attr:`line_edit`.
    """

    returnPressed = Signal()
    textChanged = Signal(str)
    textEdited = Signal(str)
    editingFinished = Signal()

    def __init__(
        self,
        placeholder: str = "",
        icon: str | None = None,
        height: int = 40,
        parent: QWidget | None = None,
        *,
        font_size: int | None = None,
        text: str = "",
    ) -> None:
        super().__init__(parent)
        self._frame = FieldFrame(height, self)
        self._edit = QLineEdit(self._frame)
        self._edit.setObjectName("mhInlineEdit")
        self._edit.setFrame(False)
        self._edit.setPlaceholderText(placeholder)
        size = font_size or (18 if height >= 52 else 16 if height >= 48 else 14)
        self._edit.setFont(fonts.font(size, 400))
        self._edit.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self._frame.set_editor(self._edit)
        self._trailing: QWidget | None = None
        self._error_label = QLabel(self)
        self._error_label.setProperty("role", "caption")
        self._error_label.setProperty("tone", "danger")
        self._error_label.setFont(fonts.role_font("caption"))
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._frame)
        lay.addWidget(self._error_label)
        self.setFocusProxy(self._edit)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if icon:
            self._frame.set_icon(icon)
        self._edit.returnPressed.connect(self.returnPressed)
        self._edit.textChanged.connect(self.textChanged)
        self._edit.textEdited.connect(self._on_edited)
        self._edit.editingFinished.connect(self.editingFinished)
        if placeholder:
            self._edit.setAccessibleName(placeholder)
        if text:
            self._edit.setText(text)

    # ------------------------------------------------------------------ accessors
    @property
    def line_edit(self) -> QLineEdit:
        """The inner QLineEdit."""
        return self._edit

    @property
    def frame(self) -> FieldFrame:
        """The painted frame."""
        return self._frame

    def text(self) -> str:
        """Current text."""
        return self._edit.text()

    def set_text(self, text: str, select: bool = False) -> None:
        """Replace the text (optionally selecting it)."""
        self._edit.setText(text)
        if select:
            self._edit.selectAll()

    def setText(self, text: str) -> None:  # noqa: N802
        self._edit.setText(text)

    def clear(self) -> None:
        """Clear the text."""
        self._edit.clear()

    def selectAll(self) -> None:  # noqa: N802
        self._edit.selectAll()

    def placeholder(self) -> str:
        """Placeholder text."""
        return self._edit.placeholderText()

    def set_placeholder(self, text: str) -> None:
        """Change the placeholder."""
        self._edit.setPlaceholderText(text)

    def set_icon(self, name: str | None) -> None:
        """Leading icon by name (None removes)."""
        self._frame.set_icon(name)

    def set_leading_color(self, color: QColor | str | None) -> None:
        """Leading colour dot (e.g. project colour); None removes."""
        self._frame.set_dot(color)

    def set_trailing_widget(self, widget: QWidget | None) -> None:
        """Place ``widget`` at the right edge inside the field (e.g. a Badge or IconButton)."""
        if self._trailing is not None:
            self._frame.layout_.removeWidget(self._trailing)
            self._trailing.setParent(None)  # type: ignore[call-overload]
        self._trailing = widget
        if widget is not None:
            widget.setParent(self._frame)
            self._frame.layout_.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
            widget.show()

    def set_completer(self, completer: QCompleter | None) -> None:
        """Attach a (styled) completer."""
        if completer is not None:
            style_completer(completer)
        self._edit.setCompleter(completer)

    def set_read_only(self, ro: bool) -> None:
        """Toggle read-only."""
        self._edit.setReadOnly(ro)

    def set_max_length(self, n: int) -> None:
        """Maximum characters."""
        self._edit.setMaxLength(n)

    # ------------------------------------------------------------------ errors
    def set_error(self, text: str | None) -> None:
        """Show (text) or clear (None/"") the error state."""
        has = bool(text)
        self._frame.set_error(has)
        theme.set_prop(self._edit, "error", "true" if has else "false")
        if has:
            self._error_label.setText(str(text))
            self._error_label.show()
            self._edit.setAccessibleDescription(str(text))
        else:
            self._error_label.hide()
            self._edit.setAccessibleDescription("")

    def has_error(self) -> bool:
        """True while an error is shown."""
        return self._error_label.isVisible() or self._frame._error  # noqa: SLF001

    def error_text(self) -> str:
        """Current error text ("" if none)."""
        return self._error_label.text() if self.has_error() else ""

    def _on_edited(self, text: str) -> None:
        if self._frame._error:  # noqa: SLF001
            self.set_error(None)
        self.textEdited.emit(text)


class TextArea(QPlainTextEdit):
    """Multi-line plain-text input (QSS-styled) sized for ``lines`` lines; Tab moves focus."""

    def __init__(self, placeholder: str = "", lines: int = 3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFont(fonts.font(14, 400))
        self.setTabChangesFocus(True)
        self._lines = lines
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._apply_height()
        if placeholder:
            self.setAccessibleName(placeholder)

    def _apply_height(self) -> None:
        fm = QFontMetrics(self.font())
        doc_margin = int(self.document().documentMargin())
        self.setFixedHeight(int(fm.lineSpacing() * self._lines + 2 * doc_margin + 12 + 2 + 2))

    def text(self) -> str:
        """Plain text."""
        return self.toPlainText()

    def set_text(self, text: str) -> None:
        """Replace the text."""
        self.setPlainText(text)


class _QuarterTimeEdit(QTimeEdit):
    """QTimeEdit whose ↑/↓ step by 15 minutes (snapping to the quarter) and wrap at midnight."""

    def stepBy(self, steps: int) -> None:  # noqa: N802
        t = self.time()
        mins = t.hour() * 60 + t.minute()
        if steps > 0:
            new = (mins // 15 + steps) * 15
        else:
            new = ((mins + 14) // 15 + steps) * 15
        new %= 24 * 60
        self.setTime(QTime(new // 60, new % 60))

    def stepEnabled(self) -> QAbstractSpinBox.StepEnabledFlag:  # noqa: N802
        return QAbstractSpinBox.StepEnabledFlag.StepUpEnabled | QAbstractSpinBox.StepEnabledFlag.StepDownEnabled


class TimeField(QWidget):
    """Time input ("HH:MM" strings): no spin buttons, ↑/↓ = ±15 min, leading clock icon.

    ``timeChanged(str)`` fires on user edits only.
    """

    timeChanged = Signal(str)

    def __init__(
        self,
        time_str: str = "09:00",
        parent: QWidget | None = None,
        *,
        use_24h: bool = True,
        height: int = 36,
        icon: str | None = "clock",
    ) -> None:
        super().__init__(parent)
        self._frame = FieldFrame(height, self)
        self._edit = _QuarterTimeEdit(self._frame)
        self._edit.setObjectName("mhInlineSpin")
        self._edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._edit.setFrame(False)
        self._edit.setFont(fonts.font(14, 500, tabular=True))
        self._edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._edit.setAccessibleName("Time")
        self._frame.set_editor(self._edit)
        if icon:
            self._frame.set_icon(icon)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._frame)
        self.setFocusProxy(self._edit)
        self.set_use_24h(use_24h)
        self.set_time_str(time_str)
        self._edit.timeChanged.connect(self._on_changed)

    @property
    def time_edit(self) -> QTimeEdit:
        """The inner QTimeEdit."""
        return self._edit

    def set_use_24h(self, use_24h: bool) -> None:
        """Switch between "HH:mm" and "h:mm AP" display."""
        self._edit.setDisplayFormat("HH:mm" if use_24h else "h:mm AP")
        fm = QFontMetrics(self._edit.font())
        sample = "00:00" if use_24h else "12:00 PM"
        lead = 16 + 8 + 12 if self._frame._icon else 12  # noqa: SLF001
        self.setFixedWidth(lead + fm.horizontalAdvance(sample) + 22)

    def time_str(self) -> str:
        """Current value as "HH:MM"."""
        return self._edit.time().toString("HH:mm")

    def set_time_str(self, value: str | None) -> None:
        """Set the value from "HH:MM" without emitting (invalid → unchanged)."""
        if not value:
            return
        t = QTime.fromString(value, "HH:mm")
        if not t.isValid():
            return
        self._edit.blockSignals(True)
        self._edit.setTime(t)
        self._edit.blockSignals(False)

    def _on_changed(self, _t: QTime) -> None:
        self.timeChanged.emit(self.time_str())


def _default_fmt(step: float, minimum: float, maximum: float) -> Callable[[float], str]:
    integral = all(float(x).is_integer() for x in (step, minimum, maximum))

    def fmt(v: float) -> str:
        if integral:
            return str(int(round(v)))
        return f"{v:g}"

    return fmt


class Stepper(QWidget):
    """Numeric stepper "−  value  +". Keyboard: ↑/→/+ and ↓/←/− step, PgUp/PgDn ×5, Home/End.

    ``fmt(value) -> str`` formats the value (``suffix`` is appended to the default format).
    ``valueChanged(float)`` fires on user changes only.
    """

    valueChanged = Signal(float)

    def __init__(
        self,
        minimum: float = 0,
        maximum: float = 100,
        step: float = 1,
        value: float | None = None,
        suffix: str = "",
        fmt: Callable[[float], str] | None = None,
        parent: QWidget | None = None,
        *,
        height: int = 36,
    ) -> None:
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._step = float(step) if step else 1.0
        self._suffix = suffix
        base = fmt or _default_fmt(self._step, self._min, self._max)
        self._fmt: Callable[[float], str] = (lambda v: base(v) + suffix) if fmt is None else base
        self._value = self._clamp(self._min if value is None else float(value))
        self._hover = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFont(fonts.font(14, 600, tabular=True))
        self.setFixedHeight(height)
        btn = height - 8
        self._minus = IconButton("minus", "Decrease", btn, self, glyph=16)
        self._plus = IconButton("plus", "Increase", btn, self, glyph=16)
        for b in (self._minus, self._plus):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(70)
        self._minus.clicked.connect(lambda: self._user_step(-1))
        self._plus.clicked.connect(lambda: self._user_step(1))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        lay.addWidget(self._minus)
        lay.addStretch(1)
        lay.addWidget(self._plus)
        self._sync()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        """Current value."""
        return self._value

    def int_value(self) -> int:
        """Current value rounded to int."""
        return int(round(self._value))

    def minimum(self) -> float:
        """Lower bound."""
        return self._min

    def maximum(self) -> float:
        """Upper bound."""
        return self._max

    def set_range(self, minimum: float, maximum: float) -> None:
        """Change bounds (value is clamped, no signal)."""
        self._min, self._max = float(minimum), float(maximum)
        self._value = self._clamp(self._value)
        self._sync()

    def set_value(self, value: float) -> None:
        """Set the value without emitting."""
        self._value = self._clamp(float(value))
        self._sync()

    def text(self) -> str:
        """Formatted value."""
        return self._fmt(self._value)

    # ------------------------------------------------------------------ internals
    def _clamp(self, v: float) -> float:
        v = max(self._min, min(self._max, v))
        n = round((v - self._min) / self._step)
        snapped = self._min + n * self._step
        snapped = max(self._min, min(self._max, snapped))
        return round(snapped, 6)

    def _user_step(self, n: int) -> None:
        new = self._clamp(self._value + n * self._step)
        if new != self._value:
            self._value = new
            self._sync()
            self.valueChanged.emit(self._value)

    def _set_user(self, v: float) -> None:
        new = self._clamp(v)
        if new != self._value:
            self._value = new
            self._sync()
            self.valueChanged.emit(self._value)

    def _sync(self) -> None:
        self._minus.setEnabled(self._value > self._min + 1e-9)
        self._plus.setEnabled(self._value < self._max - 1e-9)
        fm = QFontMetrics(self.font())
        widest = max(fm.horizontalAdvance(self._fmt(x)) for x in (self._min, self._max, self._value))
        btn = self.height() - 8
        self.setFixedWidth(8 + 2 * btn + widest + 24)
        self.setAccessibleDescription(self.text())
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width(), self.height())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._user_step(1)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Minus):
            self._user_step(-1)
        elif key == Qt.Key.Key_PageUp:
            self._user_step(5)
        elif key == Qt.Key.Key_PageDown:
            self._user_step(-5)
        elif key == Qt.Key.Key_Home:
            self._set_user(self._min)
        elif key == Qt.Key.Key_End:
            self._set_user(self._max)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync()
            if not self.isEnabled():
                self._minus.setEnabled(False)
                self._plus.setEnabled(False)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        focused = self.hasFocus()
        if focused:
            border, width = QColor(pal.accent), 2.0
        elif self._hover and enabled:
            border, width = theme.mix(pal.border_strong, pal.text_subtle, 0.6), 1.0
        else:
            border, width = QColor(pal.border_strong if enabled else pal.border), 1.0
        fill_rounded(p, QRectF(self.rect()), 8.0, pal.input if enabled else pal.surface_alt, border, width)
        p.setFont(self.font())
        p.setPen(QColor(pal.text if enabled else pal.text_subtle))
        p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.text())
        p.end()


class Slider(QSlider):
    """Themed horizontal slider; clicking the groove jumps there. ``valueReleased(int)`` fires
    when the user lets go (e.g. play a test sound)."""

    valueReleased = Signal(int)

    def __init__(
        self,
        minimum: int = 0,
        maximum: int = 100,
        value: int = 0,
        parent: QWidget | None = None,
        *,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
    ) -> None:
        super().__init__(orientation, parent)
        self.setRange(minimum, maximum)
        self.setValue(value)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(24)
        self.setMinimumWidth(120)
        self._pressed = False
        self.sliderReleased.connect(lambda: self.valueReleased.emit(self.value()))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
                span = max(1, groove.width() - handle.width())
                x = int(event.position().x()) - groove.x() - handle.width() // 2
                v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span, opt.upsideDown)
                self.setValue(v)
                self._pressed = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._pressed and not self.isSliderDown():
            self._pressed = False
            self.valueReleased.emit(self.value())


class WheelGuard(QObject):
    """Application event filter: spin boxes, sliders and combo boxes ignore the mouse wheel
    unless they have focus (the wheel scrolls the page instead)."""

    GUARDED = (QAbstractSpinBox, QAbstractSlider, QComboBox)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if et == QEvent.Type.Wheel and isinstance(obj, self.GUARDED):
            if not obj.hasFocus():  # type: ignore[attr-defined]
                event.ignore()
                return True
        elif et == QEvent.Type.Polish and isinstance(obj, self.GUARDED):
            w: QWidget = obj  # type: ignore[assignment]
            if w.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return False


def install_wheel_guard(app: QApplication) -> WheelGuard:
    """Install (once) and return the app-wide :class:`WheelGuard`."""
    existing = getattr(app, "_mh_wheel_guard", None)
    if isinstance(existing, WheelGuard):
        return existing
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    try:
        setattr(app, "_mh_wheel_guard", guard)
    except AttributeError:
        pass
    return guard

