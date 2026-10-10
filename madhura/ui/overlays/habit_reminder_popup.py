"""STUB — owned by [H]. HabitReminderPopup: due-habit reminder panel (spec §1.8).

Created by the app shell [F3] as an empty panel class; the owning agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from madhura.ui.widgets.popup import FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["HabitReminderPopup"]


class HabitReminderPopup(FloatingPanel):
    """Panel: "Daily habit" reminder panel. STUB: empty panel."""

    def __init__(self, ctx: AppContext | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__("habit")
        self._ctx = ctx
