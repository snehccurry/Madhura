"""AwayService — idle-based away detection during focus + the AwayPanel (spec §1.5).

Where the OS reports input idle time (Windows / macOS: ``attention.input_idle_seconds()`` is not
None), every engine tick while FOCUS samples it. Once idle reaches 300 s the start of the idle
stretch is remembered; when input resumes, an :class:`AwayPanel` offers to remove that time
("Away for 14 min" · [Keep as focus] [Remove 14 min] → ``engine.discard_focused``; 60 s without an
answer keeps it).

Away time must never end up credited just because nobody was there to answer, so the open idle
stretch is also the engine's *away probe* (:meth:`away_since`): a session that reaches its end
while the user is away pauses where they left instead of completing, and a sleep/suspend gap
closes the focus segment at the start of the stretch (the engine's away banner then covers the
whole absence). A user pause forgets an idle stretch in progress. A panel that is still undecided
when its session ends stays open and corrects the saved session row instead (it is cancelled when
the session is abandoned or a new one starts).
"""

from __future__ import annotations

import logging
import sqlite3
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
    :meth:`check` re-samples idle time now (tests call it after moving the FakeClock);
    :meth:`away_since` is installed as the engine's away probe; :meth:`remove_time` takes away
    time off a running or already saved session; :attr:`paused_while_away` is True while the
    session sits paused because the engine paused it for an idle stretch.
    """

    away_returned = Signal(int)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._idle_start: float | None = None
        self._sid: int | None = None
        self._panel: AwayPanel | None = None
        self._panel_sid: int | None = None
        self._away_pause_sid: int | None = None
        engine = ctx.engine
        engine.tick.connect(self.check)
        engine.state_changed.connect(self._on_state)
        engine.session_started.connect(lambda _sid: self._on_session_started())
        engine.session_finished.connect(self._on_session_finished)
        ctx.attention.changed.connect(lambda _state: self.check())
        set_probe = getattr(engine, "set_away_probe", None)
        if callable(set_probe):
            set_probe(self.away_since)

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
    def panel_session_id(self) -> int | None:
        """The session the open panel removes time from (it may have ended since)."""
        return self._panel_sid if self.panel is not None else None

    @property
    def idle_start(self) -> float | None:
        """Epoch when the current idle stretch began (None unless away right now)."""
        return self._idle_start

    def is_away(self) -> bool:
        """True while an idle stretch ≥ 300 s is in progress during FOCUS."""
        return self._idle_start is not None

    def away_since(self) -> float | None:
        """The engine's away probe: start of the idle stretch in progress in the running session."""
        engine = self._ctx.engine
        if self._idle_start is None or str(engine.state) != "focus" or engine.session_id != self._sid:
            return None
        # The engine only asks right before it pauses for an away stretch (or completes, when the
        # stretch began after the planned end): either way the next state settles this flag.
        self._away_pause_sid = engine.session_id
        return self._idle_start

    @property
    def paused_while_away(self) -> bool:
        """True while the session is paused at the start of an idle stretch (it reached its end, or
        the machine slept, while the user was away)."""
        engine = self._ctx.engine
        return (
            self._away_pause_sid is not None
            and str(engine.state) == "paused"
            and engine.session_id == self._away_pause_sid
        )

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

        sid = self._ctx.engine.session_id
        panel = self.panel
        if panel is not None and panel.decision() is None:
            if self._panel_sid == sid:
                panel.set_seconds(panel.seconds() + seconds)
                return
            self._cancel_panel()
        panel = AwayPanel(self._ctx, seconds, remover=lambda secs, s=sid: self.remove_time(s, secs))
        panel.closed.connect(lambda p=panel: self._on_panel_closed(p))
        self._panel = panel
        self._panel_sid = sid
        panel.present()

    def _on_panel_closed(self, panel: AwayPanel) -> None:
        if self._panel is panel:
            self._panel = None
            self._panel_sid = None
        panel.deleteLater()

    # ------------------------------------------------------------------ removing time
    def remove_time(self, session_id: int | None, seconds: float) -> float:
        """Take *seconds* of focus off session *session_id* (counted as paused). Returns the removed
        seconds. A running session goes through ``engine.discard_focused`` (and the check-in
        schedule restarts from the new focused time); a session that already ended has its saved
        row corrected (completed / ended early only)."""
        ctx = self._ctx
        engine = ctx.engine
        seconds = max(0.0, float(seconds))
        if session_id is None or (engine.is_active and engine.session_id == session_id):
            removed = float(engine.discard_focused(seconds))
            checkins = ctx.checkins
            if removed > 0 and checkins is not None:
                checkins.reanchor()
            return removed
        try:
            row = ctx.repo.get_session(int(session_id))
            if row is None or not row.counted:
                return 0.0
            removed = min(float(row.focused_seconds), seconds)
            if removed <= 0:
                return 0.0
            ctx.repo.update_session(
                row.id,
                focused_seconds=row.focused_seconds - removed,
                paused_seconds=row.paused_seconds + removed,
            )
        except sqlite3.Error:
            log.exception("Could not remove away time from session %s", session_id)
            return 0.0
        ctx.events.data_changed.emit("sessions")
        return removed

    # ------------------------------------------------------------------ engine
    def _on_session_started(self) -> None:
        self._sid = self._ctx.engine.session_id
        self._idle_start = None
        self._away_pause_sid = None
        self._cancel_panel()

    def _on_state(self, state: str) -> None:
        state = str(state)
        if state != "focus":
            self._idle_start = None
        if state != "paused":
            self._away_pause_sid = None

    def _on_session_finished(self, session_id: int, outcome: str, _restored: bool) -> None:
        if str(outcome) == "abandoned" and self._panel_sid == int(session_id):
            self._cancel_panel()

    def _cancel_panel(self) -> None:
        panel = self._panel
        if panel is not None and panel.decision() is None:
            try:
                panel.cancel()
            except RuntimeError:
                self._panel = None
                self._panel_sid = None
