"""STUB — owned by [S]. First-run OnboardingSheet (spec §1.11).

Created by the app shell [F3]: one welcome step that sets ``onboarded=True``; the settings agent
replaces it with the 4-step flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.labels import Label
from madhura.ui.widgets.sheet import SheetContent

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["OnboardingSheet"]


class OnboardingSheet(SheetContent):
    """Welcome sheet (ignores Esc). STUB: single step."""

    dismissible = False
    sheet_width = 480

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.set_title("Welcome to Madhura", "Plan the hour. Stay with it.")
        self.body.addWidget(Label("Plan one block at a time, and let gentle check-ins keep you honest.", "body", self))
        start = Button("Get started", "primary", parent=self)
        start.clicked.connect(self._finish)
        self.add_footer_button(start, primary=True)

    def _finish(self) -> None:
        self._ctx.settings.update(onboarded=True)
        self.close_sheet()
