"""Settings building blocks: :class:`SettingsRow` and :class:`SettingsGroup` (a card of rows)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from madhura.ui import fonts, theme
from madhura.ui.widgets.card import Card

__all__ = ["SettingsRow", "SettingsGroup", "Divider"]


class Divider(QWidget):
    """1 px horizontal rule in the ``border`` colour with optional side insets."""

    def __init__(self, parent: QWidget | None = None, *, inset: int = 0) -> None:
        super().__init__(parent)
        self._inset = inset
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self._inset, 0, self.width() - 2 * self._inset, 1, QColor(theme.palette().border))
        p.end()


class SettingsRow(QWidget):
    """Title (14/500) + optional muted description on the left, ``control`` on the right."""

    def __init__(
        self,
        title: str,
        description: str | None = None,
        control: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(24)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        text_col.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(title, self)
        self._title.setFont(fonts.font(14, 500))
        self._title.setWordWrap(True)
        self._desc = QLabel(self)
        self._desc.setProperty("role", "caption")
        self._desc.setFont(fonts.role_font("caption"))
        self._desc.setWordWrap(True)
        text_col.addWidget(self._title)
        text_col.addWidget(self._desc)
        lay.addLayout(text_col, 1)
        self._control_box = QHBoxLayout()
        self._control_box.setContentsMargins(0, 0, 0, 0)
        self._control_box.setSpacing(8)
        lay.addLayout(self._control_box, 0)
        self._control: QWidget | None = None
        self.setMinimumHeight(56)
        self.set_description(description)
        if control is not None:
            self.set_control(control)

    @property
    def title_label(self) -> QLabel:
        """The title label."""
        return self._title

    @property
    def description_label(self) -> QLabel:
        """The description label."""
        return self._desc

    @property
    def control(self) -> QWidget | None:
        """The control widget."""
        return self._control

    def set_title(self, text: str) -> None:
        """Change the title."""
        self._title.setText(text)

    def set_description(self, text: str | None) -> None:
        """Set/clear the description line."""
        self._desc.setText(text or "")
        self._desc.setVisible(bool(text))

    def set_control(self, widget: QWidget | None) -> None:
        """Replace the right-hand control."""
        if self._control is not None:
            self._control_box.removeWidget(self._control)
            self._control.setParent(None)  # type: ignore[call-overload]
        self._control = widget
        if widget is not None:
            self._control_box.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            if not widget.accessibleName():
                widget.setAccessibleName(self._title.text())

    def add_control(self, widget: QWidget) -> None:
        """Add an extra control to the right (e.g. a "Test" button next to a slider)."""
        self._control_box.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)


class SettingsGroup(QWidget):
    """A titled card of settings rows separated by inset dividers."""

    def __init__(self, title: str = "", description: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QVBoxLayout()
        head.setSpacing(2)
        head.setContentsMargins(2, 0, 0, 0)
        self._title = QLabel(title, self)
        self._title.setProperty("role", "h2")
        self._title.setFont(fonts.role_font("h2"))
        self._title.setVisible(bool(title))
        head.addWidget(self._title)
        self._desc = QLabel(description or "", self)
        self._desc.setProperty("role", "caption")
        self._desc.setFont(fonts.role_font("caption"))
        self._desc.setWordWrap(True)
        self._desc.setVisible(bool(description))
        head.addWidget(self._desc)
        lay.addLayout(head)
        self.card = Card(padding=0, parent=self)
        self.card.body.setSpacing(0)
        lay.addWidget(self.card)
        self._items: list[QWidget] = []

    def set_title(self, title: str) -> None:
        """Change the group title."""
        self._title.setText(title)
        self._title.setVisible(bool(title))

    def add_row(self, row: QWidget) -> QWidget:
        """Append a row (a divider is inserted automatically)."""
        if self._items:
            self.card.body.addWidget(Divider(self.card, inset=20))
        self.card.body.addWidget(row)
        self._items.append(row)
        return row

    def add(self, title: str, description: str | None = None, control: QWidget | None = None) -> SettingsRow:
        """Create, append and return a :class:`SettingsRow`."""
        row = SettingsRow(title, description, control, self.card)
        self.add_row(row)
        return row

    def add_widget(self, widget: QWidget) -> QWidget:
        """Append an arbitrary widget as a row."""
        return self.add_row(widget)

    def rows(self) -> list[QWidget]:
        """Rows in order."""
        return list(self._items)
