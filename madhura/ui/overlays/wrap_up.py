"""STUB — owned by [I]. WrapUpSheet "Wrap up the day" (spec §1.9).

Created by the app shell [F3]: a single "Shutdown complete" action; the insights agent replaces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.sheet import SheetContent

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["WrapUpSheet"]


class WrapUpSheet(SheetContent):
    """End-of-day review sheet. STUB."""

    sheet_width = 480

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.set_title("Wrap up the day", "Review today and set up tomorrow's first block.")
        cancel = Button("Not now", "ghost", parent=self)
        cancel.clicked.connect(self.close_sheet)
        done = Button("Shutdown complete", "primary", parent=self)
        done.clicked.connect(self._shutdown)
        self.add_footer_button(cancel)
        self.add_footer_button(done, primary=True)

    def _shutdown(self) -> None:
        if self._ctx.nudges is not None:
            self._ctx.nudges.done_for_today()
        self.close_sheet()
