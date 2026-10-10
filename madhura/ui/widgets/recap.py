"""STUB — owned by [I]. WeeklyRecapCard shown on the Focus page in a new ISO week.

Created by the app shell [F3]; never asks to be shown. The insights agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from madhura.ui.widgets.card import Card

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["WeeklyRecapCard"]


class WeeklyRecapCard(Card):
    """"Last week: 11h 20m (+18%) · best day Thu …" + dismiss. STUB."""

    dismissed = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(title="Last week", parent=parent)
        self._ctx = ctx

    def should_show(self) -> bool:
        """True on the first open in a new ISO week (kv 'weekly_recap_seen'). STUB: False."""
        return False

    def dismiss(self) -> None:
        """Hide and remember the dismissal."""
        self.hide()
        self.dismissed.emit()
