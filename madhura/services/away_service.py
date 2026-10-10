"""STUB — owned by [O]. AwayService: idle-based away detection + AwayPanel (spec §1.5).

Created by the app shell [F3] as a no-op; the overlays agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["AwayService"]


class AwayService(QObject):
    """Offers to remove idle time from a running session (``ctx.away``). STUB: does nothing."""

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
