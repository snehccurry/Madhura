"""STUB — owned by [O]. Mini timer pill: FocusPill (FloatingPanel) + PillController (spec §1.6).

Created by the app shell [F3] with the full controller API; the pill is never shown. The overlays
agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from madhura.ui.widgets.popup import FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["FocusPill", "PillController"]


class FocusPill(FloatingPanel):
    """Compact always-on-top timer (36 px tall, radius 18). STUB: empty panel."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__("pill", width=120, padding=0, radius=18)
        self._ctx = ctx


class PillController(QObject):
    """Decides when the pill is visible (``ctx.pill``). STUB: never shows it."""

    visibility_changed = Signal(bool)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._user_hidden = False
        ctx.engine.state_changed.connect(self._on_state)

    def _on_state(self, state: str) -> None:
        if state == "idle":
            self._user_hidden = False

    def toggle_user_visible(self) -> None:
        """Tray "Mini timer: Show/Hide" (resets when the session ends)."""
        self._user_hidden = not self._user_hidden

    def is_showing(self) -> bool:
        """True while the pill is on screen. STUB: False."""
        return False

    def pulse(self) -> None:
        """Pulse the border once (2 minutes left). STUB: no-op."""
