"""STUB — owned by [O]. NudgeService: planning nudges between blocks (spec §1.7, §5.3).

Created by the app shell [F3]: snooze / done-for-today state works (kv 'nudge_snooze_until',
'nudge_done_date'), but no nudge is ever shown. The overlays agent replaces this module.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from madhura.core.models import from_iso, to_date_str, to_iso

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["NudgeService", "SNOOZE_KEY", "DONE_KEY"]

SNOOZE_KEY = "nudge_snooze_until"
DONE_KEY = "nudge_done_date"


class NudgeService(QObject):
    """Nudges the user to plan the next block (``ctx.nudges``). STUB: never nudges."""

    state_changed = Signal()

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx

    # ------------------------------------------------------------------ state

    @property
    def snoozed_until(self) -> datetime | None:
        """End of the current snooze, or None."""
        try:
            until = from_iso(self._ctx.repo.kv_get(SNOOZE_KEY))
        except (TypeError, ValueError):
            return None
        if until is None or until <= self._ctx.clock.now():
            return None
        return until

    @property
    def is_done_for_today(self) -> bool:
        """True after "Done for today" on the current logical day."""
        return self._ctx.repo.kv_get(DONE_KEY) == to_date_str(self._ctx.today())

    @property
    def banner_visible(self) -> bool:
        """True while the firm-nudge banner should show. STUB: False."""
        return False

    # ------------------------------------------------------------------ commands

    def snooze(self, minutes: int) -> None:
        """Snooze nudges for *minutes*."""
        until = (self._ctx.clock.now() + timedelta(minutes=int(minutes))).replace(microsecond=0)
        self._ctx.repo.kv_set(SNOOZE_KEY, to_iso(until))
        self.state_changed.emit()

    def done_for_today(self) -> None:
        """Stop nudging until the next logical day."""
        self._ctx.repo.kv_set(DONE_KEY, to_date_str(self._ctx.today()))
        self.state_changed.emit()

    def resume(self) -> None:
        """Clear snooze and done-for-today."""
        self._ctx.repo.kv_set(SNOOZE_KEY, None)
        self._ctx.repo.kv_set(DONE_KEY, None)
        self.state_changed.emit()

    def primary_action(self) -> tuple[str, Callable[[], None]]:
        """Label + callback of the one-click primary action. STUB: always "Plan now"."""
        return "Plan now", self._plan_now

    def _plan_now(self) -> None:
        window = self._ctx.window
        if window is not None:
            window.show_main("focus", activate=True)
            window.focus_task_input()
