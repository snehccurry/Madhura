"""STUB — owned by [O]. CheckInPopup: non-activating check-in panel (spec §1.5).

Created by the app shell [F3] as an empty panel class; the owning agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from madhura.ui.widgets.popup import FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["CheckInPopup"]


class CheckInPopup(FloatingPanel):
    """Panel: "Still on <task>?" check-in panel. STUB: empty panel."""

    def __init__(self, ctx: AppContext | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__("checkin")
        self._ctx = ctx
