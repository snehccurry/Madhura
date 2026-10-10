"""STUB — owned by [H]. HabitsPage (spec §1.8).

Created by the app shell [F3]: header + empty state + a plain list of habit names; the habits
agent replaces this module. The shell calls ``new_habit()`` for Ctrl+N and ``on_shown()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from madhura.ui.pages.habit_editor import HabitEditorSheet
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.empty_state import EmptyState
from madhura.ui.widgets.labels import Label
from madhura.ui.widgets.page import PageHeader, PageScroll

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["HabitsPage", "MOTTO"]

MOTTO = "Improve by 1% every day — by the end of the year, you'll reach ~37x growth."


class HabitsPage(PageScroll):
    """Daily habits page (max width 760). STUB."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(760, parent)
        self._ctx = ctx
        self._dirty = True
        new = Button("New habit", "primary", icon="plus", parent=self.column)
        new.clicked.connect(self.new_habit)
        self.header = PageHeader("Habits", [new], self.column, subtitle=MOTTO)
        self.add(self.header)
        self.content_layout.addSpacing(8)
        action = Button("New habit", "primary", icon="plus", parent=self.column)
        action.clicked.connect(self.new_habit)
        self.empty = EmptyState("sparkle", "Small habits, big results", MOTTO, action, self.column)
        self.add(self.empty)
        self.list_card = Card(parent=self.column)
        self.add(self.list_card)
        ctx.events.data_changed.connect(self._on_data_changed)
        self.refresh()

    def _on_data_changed(self, kind: str) -> None:
        if kind in ("habits", "all"):
            self._dirty = True
            if self.isVisible():
                self.refresh()

    def on_shown(self) -> None:
        """Refresh when data changed while hidden."""
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        """Rebuild the habit list."""
        self._dirty = False
        habits = self._ctx.repo.habits()
        body = self.list_card.body
        while body.count():
            item = body.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for h in habits:
            body.addWidget(Label(f"{h.emoji or ''} {h.name}".strip(), "body", self.list_card))
        self.empty.setVisible(not habits)
        self.list_card.setVisible(bool(habits))

    def new_habit(self) -> None:
        """Open the editor for a new habit (Ctrl+N on this page)."""
        if self._ctx.window is not None:
            self._ctx.window.show_sheet(HabitEditorSheet(self._ctx))
