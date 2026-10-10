"""AwayService — idle-based away detection during focus + the AwayPanel (spec §1.5).

Where the OS reports input idle time (Windows / macOS: ``attention.input_idle_seconds()`` is not
None), every engine tick while FOCUS samples it. Once idle reaches 300 s the start of the idle
stretch is remembered; when input resumes, an :class:`AwayPanel` offers to remove that time
("Away for 14 min" · [Keep as focus] [Remove 14 min] → ``engine.discard_focused``; 60 s without an
answer keeps it). Pausing or ending the session forgets an idle stretch in progress (the engine's
own away banner covers sleep/suspend gaps).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.ui.overlays.away_panel import AwayPanel

__all__ = ["AwayService", "AWAY_IDLE_SECONDS"]

log = logging.getLogger(__name__)

#: Idle this long during FOCUS counts as being away.
AWAY_IDLE_SECONDS = 300.0


class AwayService(QObject):
    """Offers to remove idle time from a running session (``ctx.away``).

    Extensions: ``away_returned(seconds)`` when an away stretch ends and the panel is offered;
    :meth:`check` re-samples idle time now (tests call it after moving the FakeClock).
    """

    away_returned = Signal(int)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._idle_start: float | None = None
        self._sid: int | None = None
        self._panel: AwayPanel | None = None
        ctx.engine.tick.connect(self.check)
        ctx.engine.state_changed.connect(self._on_state)
        ctx.engine.session_started.connect(lambda _sid: self._on_session_started())
        ctx.attention.changed.connect(lambda _state: self.check())

    # ------------------------------------------------------------------ queries
    @property
    def panel(self) -> AwayPanel | None:
        """The away panel while it is open (including its short "Removed" confirmation)."""
        panel = self._panel
        if panel is not None:
            try:
                if not panel.is_open() or panel.is_closing():
                    return None
            except RuntimeError:
                self._panel = None
                return None
        return panel

    @property
    def idle_start(self) -> float | None:
        """Epoch when the current idle stretch began (None unless away right now)."""
        return self._idle_start

    def is_away(self) -> bool:
        """True while an idle stretch ≥ 300 s is in progress during FOCUS."""
        return self._idle_start is not None

    # ------------------------------------------------------------------ sampling
    def check(self) -> None:
        """Sample input idle time; start or end an away stretch."""
        ctx = self._ctx
        engine = ctx.engine
        if str(engine.state) != "focus":
            return
        idle = ctx.attention.input_idle_seconds()
        if idle is None:
            return
        now = ctx.clock.time()
        idle = max(0.0, float(idle))
        if engine.session_id != self._sid:
            self._sid = engine.session_id
            self._idle_start = None
        if idle >= AWAY_IDLE_SECONDS:
            if self._idle_start is None:
                self._idle_start = now - idle
            return
        if self._idle_start is None:
            return
        returned_at = now - idle
        away = returned_at - self._idle_start
        self._idle_start = None
        if away < AWAY_IDLE_SECONDS:
            return
        offer = min(away, float(engine.focused_seconds))
        if offer < 60:
            return
        self.away_returned.emit(int(round(offer)))
        self._offer(offer)

    def _offer(self, seconds: float) -> None:
        from madhura.ui.overlays.away_panel import AwayPanel

        panel = self.panel
        if panel is not None and panel.decision() is None:
            panel.set_seconds(panel.seconds() + seconds)
            return
        panel = AwayPanel(self._ctx, seconds)
        panel.closed.connect(lambda p=panel: self._on_panel_closed(p))
        self._panel = panel
        panel.present()

    def _on_panel_closed(self, panel: AwayPanel) -> None:
        if self._panel is panel:
            self._panel = None
        panel.deleteLater()

    # ------------------------------------------------------------------ engine
    def _on_session_started(self) -> None:
        self._sid = self._ctx.engine.session_id
        self._idle_start = None
        self._cancel_panel()

    def _on_state(self, state: str) -> None:
        state = str(state)
        if state != "focus":
            self._idle_start = None
        if state in ("idle", "break"):
            self._cancel_panel()

    def _cancel_panel(self) -> None:
        panel = self._panel
        if panel is not None and panel.decision() is None:
            try:
                panel.cancel()
            except RuntimeError:
                self._panel = None
