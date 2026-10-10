"""STUB — owned by [I]. Custom-painted charts: BarChart, Heatmap, HBarList.

Created by the app shell [F3] as blank placeholders that accept data; the insights agent replaces
this module.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget

__all__ = ["BarChart", "Heatmap", "HBarList"]


class _ChartStub(QWidget):
    """Blank widget that remembers the last data it was given."""

    _height = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data: Any = None
        self.setMinimumHeight(self._height)

    def set_data(self, *args: Any, **kwargs: Any) -> None:
        """Store the chart data (STUB: not drawn)."""
        self.data = (args, kwargs)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, self._height)


class BarChart(_ChartStub):
    """Daily / weekly focus bars with a goal line. STUB."""

    _height = 260


class Heatmap(_ChartStub):
    """Weekday × hour focus heatmap. STUB."""

    _height = 180


class HBarList(_ChartStub):
    """Horizontal bar list (top projects, drift reasons). STUB."""

    _height = 120
