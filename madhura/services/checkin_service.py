"""STUB — owned by [O]. CheckInService: schedules and presents focus check-ins (spec §1.5, §5.3).

Created by the app shell [F3] with the full public API but no scheduling (never prompts); the
overlays agent replaces this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["CheckInService"]

log = logging.getLogger(__name__)


class CheckInService(QObject):
    """Fires check-ins during FOCUS (``ctx.checkins``). STUB: never prompts."""

    prompt_inline = Signal()
    prompt_closed = Signal()
    answered = Signal(str)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx

    def answer(self, response: str, reason: str | None = None) -> None:
        """Record an answer for the running session and emit ``answered``."""
        engine = self._ctx.engine
        sid = engine.session_id
        if sid is None or not engine.is_active:
            return
        self._ctx.repo.add_checkin(sid, self._ctx.clock.now().replace(microsecond=0), response, reason)
        self._ctx.events.data_changed.emit("checkins")
        self.answered.emit(str(response))

    def counts(self) -> dict:
        """Current session's {"focused", "drifted", "missed"} counts."""
        sid = self._ctx.engine.session_id
        if sid is None:
            return {"focused": 0, "drifted": 0, "missed": 0}
        return dict(self._ctx.repo.checkin_counts(sid))

    def seconds_until_next(self) -> float | None:
        """Seconds of focus until the next check-in (None when off / not focusing). STUB: None."""
        return None

    def timeout_progress(self) -> float | None:
        """0..1 remaining of the open prompt's timeout (None when no prompt). STUB: None."""
        return None
