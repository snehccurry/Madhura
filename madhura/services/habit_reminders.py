"""STUB — owned by [H]. HabitReminderService: timed habit reminders (spec §1.8).

Created by the app shell [F3] as a no-op; the habits agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["HabitReminderService"]


class HabitReminderService(QObject):
    """Reminds about due habits (``ctx.habit_reminders``). STUB: never reminds."""

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._queued: list[int] = []

    @property
    def queued_count(self) -> int:
        """Reminders held back (e.g. during focus)."""
        return len(self._queued)

    def reset(self) -> None:
        """Forget queued reminders and snoozes (after a data reset)."""
        self._queued.clear()
