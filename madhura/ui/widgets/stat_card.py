"""STUB — owned by [I]. StatCard: KPI tile (label, value, delta, hint).

Created by the app shell [F3] as a simple Card; the insights agent replaces this module.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from madhura.ui import fonts
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.labels import Caption, Micro

__all__ = ["StatCard"]


class StatCard(Card):
    """KPI tile: micro-caps label, 28/600 value, delta + hint caption. STUB."""

    def __init__(self, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(padding=(20, 16, 20, 16), parent=parent)
        self.setFixedHeight(108)
        self._label = Micro(label, self)
        self._value = QLabel("—", self)
        self._value.setFont(fonts.font(28, 600, tabular=True))
        self._hint = Caption("", self)
        self.body.setSpacing(4)
        self.body.addWidget(self._label)
        self.body.addWidget(self._value)
        self.body.addWidget(self._hint)

    def set_value(self, text: str) -> None:
        """Main value text."""
        self._value.setText(text)

    def value_text(self) -> str:
        """Main value text."""
        return self._value.text()

    def set_delta(self, delta: float | None, caption: str = "") -> None:
        """Delta vs previous period (STUB: shown as plain caption)."""
        self._hint.setText(caption)

    def set_hint(self, text: str) -> None:
        """Hint caption."""
        self._hint.setText(text)
