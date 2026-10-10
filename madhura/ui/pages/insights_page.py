"""STUB — owned by [I]. InsightsPage (spec §1.9).

Created by the app shell [F3]: header + empty state; the insights agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.chips import SegmentedControl
from madhura.ui.widgets.empty_state import EmptyState
from madhura.ui.widgets.page import PageHeader, PageScroll

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["InsightsPage"]


class InsightsPage(PageScroll):
    """Focus analytics page (max width 1120). STUB."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(1120, parent)
        self._ctx = ctx
        self.range_control = SegmentedControl(
            [("7d", "7 days"), ("30d", "30 days"), ("12w", "12 weeks")], "7d", self.column, size="sm"
        )
        self.header = PageHeader("Insights", [self.range_control], self.column)
        self.add(self.header)
        self.content_layout.addSpacing(8)
        plan = Button("Plan a block", "primary", icon="play", parent=self.column)
        plan.clicked.connect(lambda: ctx.events.navigate.emit("focus"))
        self.empty = EmptyState(
            "chart",
            "Your focus story starts here",
            "Finish a focus block and your totals, streaks and patterns appear here.",
            plan,
            self.column,
        )
        self.add(self.empty)

    def on_shown(self) -> None:
        """Refresh when shown. STUB: nothing to refresh."""
