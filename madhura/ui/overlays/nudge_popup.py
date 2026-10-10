"""STUB — owned by [O]. NudgePopup: gentle planning nudge panel (spec §1.7).

Created by the app shell [F3] as an empty panel class; the owning agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from madhura.ui.widgets.popup import FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["NudgePopup"]


class NudgePopup(FloatingPanel):
    """Panel: "What's next?" planning nudge panel. STUB: empty panel."""

    def __init__(self, ctx: AppContext | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__("nudge")
        self._ctx = ctx
