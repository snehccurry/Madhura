"""Select: a compact dropdown (button + chevron) that opens a styled :class:`Menu`."""

from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from madhura.ui import fonts, icons, theme
from madhura.ui.widgets._common import HoverFader, KeyboardFocusMixin, elide, fill_rounded
from madhura.ui.widgets.chips import normalize_options
from madhura.ui.widgets.menu import Menu

__all__ = ["Select"]


class Select(KeyboardFocusMixin, QAbstractButton):
    """Dropdown picker over ``[(value, label), …]``.

    Click/Space/Enter/Alt+↓ opens the menu (current value checked); ↑/↓ change the value directly.
    ``valueChanged(object)`` fires on user changes only.
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        options: Iterable[Any] = (),
        value: Any = None,
        parent: QWidget | None = None,
        *,
        placeholder: str = "Select…",
        height: int = 36,
        min_width: int = 0,
    ) -> None:
        super().__init__(parent)
        self._options: list[tuple[Any, str]] = []
        self._value: Any = None
        self._placeholder = placeholder
        self._min_width = min_width
        self._menu: Menu | None = None
        self._hover = HoverFader(self, 120)
        self._ring_radius = 8.0
        self.setFont(fonts.font(13, 500))
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self.open_menu)
        self.set_options(options, value)

    # ------------------------------------------------------------------ API
    def options(self) -> list[tuple[Any, str]]:
        """Current ``(value, label)`` list."""
        return list(self._options)

    def set_options(self, options: Iterable[Any], value: Any = None) -> None:
        """Replace options; keeps the current value when still present unless ``value`` given."""
        self._options = normalize_options(options)
        values = [v for v, _ in self._options]
        if value is not None and value in values:
            self._value = value
        elif self._value not in values:
            self._value = None
        self._refresh()

    def value(self) -> Any:
        """Selected value (None if none)."""
        return self._value

    def set_value(self, value: Any) -> None:
        """Select ``value`` without emitting (unknown values are ignored)."""
        if value in [v for v, _ in self._options]:
            self._value = value
            self._refresh()

    def current_label(self) -> str:
        """Label of the selected option ("" if none)."""
        for v, lab in self._options:
            if v == self._value:
                return lab
        return ""

    def menu(self) -> Menu | None:  # type: ignore[override]
        """The last opened menu (for tests)."""
        return self._menu

    # ------------------------------------------------------------------ behaviour
    def _refresh(self) -> None:
        self.setAccessibleDescription(self.current_label())
        self.setFixedWidth(self.sizeHint().width())
        self.update()

    def _choose(self, value: Any) -> None:
        if value != self._value:
            self._value = value
            self._refresh()
            self.valueChanged.emit(value)

    def open_menu(self) -> None:
        """Open the option menu below the control."""
        menu = Menu(self)
        menu.set_min_width(self.width())
        for v, lab in self._options:
            menu.add_action(lab, lambda v=v: self._choose(v), checkable=True, checked=(v == self._value))
        self._menu = menu
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup_below(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        values = [v for v, _ in self._options]
        if key == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.open_menu()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and values:
            idx = values.index(self._value) if self._value in values else -1
            idx = max(0, idx - 1) if key == Qt.Key.Key_Up else min(len(values) - 1, idx + 1)
            self._choose(values[idx])
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_menu()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ sizing + paint
    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        labels = [lab for _v, lab in self._options] + [self._placeholder]
        w = max((fm.horizontalAdvance(lab) for lab in labels), default=40)
        return QSize(max(self._min_width, 12 + w + 8 + 16 + 10), self.height())

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        t = 1.0 if self.isDown() else self._hover.value
        fill_rounded(p, QRectF(self.rect()), 8.0, theme.mix(pal.surface_alt, pal.hover, t), pal.border_strong)
        label = self.current_label()
        p.setFont(self.font())
        p.setPen(QColor(pal.text if label else pal.text_subtle))
        fm = QFontMetrics(self.font())
        text_w = self.width() - 12 - 8 - 16 - 10
        p.drawText(
            QRectF(12, 0, text_w, self.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elide(label or self._placeholder, fm, text_w),
        )
        pm = icons.pixmap("chevron_down", 16, theme.mix(pal.text_muted, pal.text, t), self.devicePixelRatioF())
        p.drawPixmap(self.width() - 10 - 16, (self.height() - 16) // 2, pm)
        p.end()
