"""CheckInService — schedules and presents focus check-ins (spec §1.5, §5.3, §7.3).

Timing comes from :class:`madhura.core.checkins.CheckInPlanner` on the engine's *focused*
seconds, evaluated on every ``engine.tick`` (one per displayed second while focusing — paused time
never advances it). When a check-in is due:

* input idle ≥ 300 s → suppressed (no record; the AwayService handles the idle time);
* attention "busy" / "quiet" / "away" → deferred and re-tested every 15 s; a deferred check-in
  that is overtaken by the next one is dropped (no record) so at most one shows afterwards;
* main window active on the Focus page (no sheet) → ``prompt_inline`` (the Focus page shows it
  and answers through :meth:`answer`); otherwise a :class:`CheckInPopup` in the popup stack.

The open prompt times out after ``checkin_timeout_seconds`` of clock time (hovering the popup
pauses it, resuming with ≥ 10 s) → 'missed'. Two consecutive misses pause the session at the
fire time of the first one. Pausing or ending the session closes an open prompt without a record.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from madhura.core.checkins import MIN_REMAINING_SECONDS, CheckInPlanner

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.ui.overlays.checkin_popup import CheckInPopup

__all__ = [
    "CheckInService",
    "INTRO_KEY",
    "RESPONSES",
    "IDLE_SUPPRESS_SECONDS",
    "DEFER_RETEST_MS",
    "HOVER_RESUME_SECONDS",
]

log = logging.getLogger(__name__)

INTRO_KEY = "checkin_intro_seen"
RESPONSES = ("focused", "drifted", "missed")
IDLE_SUPPRESS_SECONDS = 300.0
DEFER_RETEST_MS = 15_000
HOVER_RESUME_SECONDS = 10.0
#: Attention states that defer a due check-in.
_DEFER_STATES = ("busy", "quiet", "away")


@dataclass
class _Prompt:
    """An open (unanswered) check-in."""

    session_id: int
    mode: str  # "inline" | "popup"
    fired_epoch: float
    fired_at: datetime
    timeout: float
    deadline: float
    hover_left: float | None = None  # seconds left, frozen while the popup is hovered
    answering: bool = False  # "Focusing back" chosen in the popup: no timeout any more


class CheckInService(QObject):
    """Fires check-ins during FOCUS (``ctx.checkins``); see the module docstring.

    Signals: ``prompt_inline`` (Focus page: show the inline prompt), ``prompt_closed`` (hide an
    unanswered inline prompt), ``answered(response)`` (after every recorded answer, including
    'missed'). Extensions: ``auto_paused`` (two misses paused the session) and
    ``prompt_opened(mode)`` ("inline" | "popup").
    """

    prompt_inline = Signal()
    prompt_closed = Signal()
    answered = Signal(str)
    auto_paused = Signal()
    prompt_opened = Signal(str)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        s = ctx.settings
        self._planner = CheckInPlanner(int(s.get("checkin_interval_minutes")), bool(s.get("checkin_adaptive")))
        self._sid: int | None = None
        self._prompt: _Prompt | None = None
        self._popup: CheckInPopup | None = None
        self._deferred = False
        self._misses = 0
        self._first_miss_epoch: float | None = None
        self._miss_pause_sid: int | None = None
        self._defer_timer = QTimer(self)
        self._defer_timer.setInterval(DEFER_RETEST_MS)
        self._defer_timer.timeout.connect(self.evaluate)

        engine = ctx.engine
        engine.session_started.connect(self._on_session_started)
        engine.state_changed.connect(self._on_state_changed)
        engine.tick.connect(self.evaluate)
        s.changed.connect(self._on_setting)
        ctx.attention.changed.connect(self._on_attention)
        if engine.is_active and engine.session_id is not None:
            self._adopt_session()

    # ================================================================== public API
    def answer(self, response: str, reason: str | None = None) -> None:
        """Record *response* ('focused' | 'drifted' | 'missed') for the running session.

        Closes the open prompt (an inline prompt is not told — it shows its own confirmation),
        updates adaptive spacing and emits ``answered``.
        """
        response = str(response)
        if response not in RESPONSES:
            log.warning("Ignoring unknown check-in response %r", response)
            return
        engine = self._ctx.engine
        sid = engine.session_id
        self._drop_prompt(notify=False)
        if sid is None or not engine.is_active:
            return
        self._record(sid, response, reason or None)
        self._planner.record(response)
        if response != "missed":
            self._misses = 0
            self._first_miss_epoch = None
        self.answered.emit(response)

    def counts(self) -> dict:
        """Current session's ``{"focused", "drifted", "missed"}`` counts."""
        sid = self._ctx.engine.session_id
        if sid is None:
            return {"focused": 0, "drifted": 0, "missed": 0}
        try:
            return dict(self._ctx.repo.checkin_counts(sid))
        except sqlite3.Error:
            log.exception("Could not read check-in counts")
            return {"focused": 0, "drifted": 0, "missed": 0}

    def seconds_until_next(self) -> float | None:
        """Focused seconds until the next check-in (None when off / not focusing)."""
        engine = self._ctx.engine
        if not self._enabled() or str(engine.state) != "focus":
            return None
        self._sync_session()
        return self._planner.seconds_until_next(engine.focused_seconds)

    def timeout_progress(self) -> float | None:
        """0..1 of ``checkin_timeout_seconds`` left on the open prompt (None when none is open)."""
        p = self._prompt
        if p is None:
            return None
        total = float(self._ctx.settings.get("checkin_timeout_seconds")) or p.timeout
        if p.answering:
            return 1.0
        return max(0.0, min(1.0, self._seconds_left(p) / max(1.0, total)))

    # ================================================================== extensions
    @property
    def planner(self) -> CheckInPlanner:
        """The underlying planner (read-only use)."""
        return self._planner

    @property
    def prompt_mode(self) -> str | None:
        """"inline" | "popup" while a prompt is open, else None."""
        return self._prompt.mode if self._prompt is not None else None

    @property
    def popup(self) -> CheckInPopup | None:
        """The check-in panel while it is on screen (also during its confirmation)."""
        return self._popup

    @property
    def is_deferred(self) -> bool:
        """True while a due check-in waits for attention to return."""
        return self._deferred

    @property
    def paused_by_misses(self) -> bool:
        """True while the session is paused because two check-ins in a row were missed."""
        engine = self._ctx.engine
        return (
            self._miss_pause_sid is not None
            and str(engine.state) == "paused"
            and engine.session_id == self._miss_pause_sid
        )

    def seconds_left(self) -> float | None:
        """Clock seconds until the open prompt times out (None when none is open)."""
        return self._seconds_left(self._prompt) if self._prompt is not None else None

    def set_prompt_hover(self, hovered: bool) -> None:
        """The popup is hovered: freeze the timeout; on leave resume with at least 10 s."""
        p = self._prompt
        if p is None or p.answering:
            return
        now = self._ctx.clock.time()
        if hovered:
            if p.hover_left is None:
                p.hover_left = max(0.0, p.deadline - now)
        elif p.hover_left is not None:
            p.deadline = now + max(p.hover_left, HOVER_RESUME_SECONDS)
            p.hover_left = None

    def begin_reason(self) -> None:
        """"Focusing back" was chosen in the popup: the answer is drifted, stop the timeout."""
        if self._prompt is not None:
            self._prompt.answering = True
            self._prompt.hover_left = None

    def fire_now(self) -> bool:
        """Present a check-in immediately (ignores timing; still needs a focusing session)."""
        if str(self._ctx.engine.state) != "focus" or self._prompt is not None:
            return False
        self._sync_session()
        self._fire()
        return True

    # ================================================================== evaluation
    def evaluate(self) -> None:
        """Advance prompt timeouts and fire a due check-in (called on every engine tick)."""
        ctx = self._ctx
        engine = ctx.engine
        if str(engine.state) != "focus" or not self._enabled():
            self._defer_timer.stop()
            return
        self._sync_session()
        if self._prompt is not None:
            self._service_prompt()
            return
        focused = engine.focused_seconds
        remaining = engine.remaining_seconds
        if not self._planner.is_due(focused, remaining):
            if self._deferred and remaining < MIN_REMAINING_SECONDS:
                self._set_deferred(False)
            return
        idle = ctx.attention.input_idle_seconds()
        if idle is not None and idle >= IDLE_SUPPRESS_SECONDS:
            # Away from the keyboard: no prompt, no record; schedule the next one.
            self._planner.fired(focused)
            self._set_deferred(False)
            return
        if ctx.attention.state() in _DEFER_STATES:
            # Drop deferred check-ins overtaken by the next due time (no record).
            interval = self._planner.current_interval
            while focused >= self._planner.next_due + interval:
                self._planner.fired(self._planner.next_due)
            self._set_deferred(True)
            return
        self._fire()

    def _set_deferred(self, deferred: bool) -> None:
        self._deferred = deferred
        if deferred and not self._defer_timer.isActive():
            self._defer_timer.start()
        elif not deferred:
            self._defer_timer.stop()

    def _fire(self) -> None:
        ctx = self._ctx
        engine = ctx.engine
        sid = engine.session_id
        if sid is None:
            return
        focused = engine.focused_seconds
        self._planner.fired(focused)
        self._set_deferred(False)
        now = ctx.clock.time()
        timeout = float(ctx.settings.get("checkin_timeout_seconds"))
        mode = "inline" if self._inline_possible() else "popup"
        self._prompt = _Prompt(
            session_id=sid,
            mode=mode,
            fired_epoch=now,
            fired_at=ctx.clock.now().replace(microsecond=0),
            timeout=timeout,
            deadline=now + timeout,
        )
        try:
            ctx.sound.play("checkin")
        except Exception:  # noqa: BLE001 - sound must never break a check-in
            log.debug("check-in sound failed", exc_info=True)
        if mode == "inline":
            self.prompt_inline.emit()
        else:
            self._show_popup()
        self.prompt_opened.emit(mode)

    def _inline_possible(self) -> bool:
        window = self._ctx.window
        if window is None:
            return False
        try:
            return bool(window.is_main_active() and window.current_page() == "focus" and not window.has_sheet())
        except RuntimeError:  # window torn down
            return False

    def _show_popup(self) -> None:
        from madhura.ui.overlays.checkin_popup import CheckInPopup

        ctx = self._ctx
        engine = ctx.engine
        intro = False
        try:
            if not ctx.repo.kv_get(INTRO_KEY):
                intro = True
                ctx.repo.kv_set(INTRO_KEY, True)
        except sqlite3.Error:
            log.debug("intro kv failed", exc_info=True)
        self._close_popup()
        popup = CheckInPopup(ctx, self)
        popup.set_question(engine.task, engine.focused_seconds, engine.remaining_seconds, intro=intro)
        popup.closed.connect(lambda p=popup: self._on_popup_closed(p))
        self._popup = popup
        popup.present()

    def _service_prompt(self) -> None:
        """Timeout + inline→popup migration for the open prompt."""
        p = self._prompt
        if p is None:
            return
        if p.mode == "inline" and not self._inline_possible():
            # The window went away mid-question: carry the same prompt over to a popup.
            self.prompt_closed.emit()
            p.mode = "popup"
            self._show_popup()
            return
        if p.answering or p.hover_left is not None:
            return
        if self._ctx.clock.time() >= p.deadline:
            self._on_timeout()

    def _seconds_left(self, p: _Prompt) -> float:
        if p.hover_left is not None:
            return p.hover_left
        return max(0.0, p.deadline - self._ctx.clock.time())

    def _on_timeout(self) -> None:
        p = self._prompt
        if p is None:
            return
        ctx = self._ctx
        self._prompt = None
        if p.mode == "inline":
            self.prompt_closed.emit()
        else:
            self._close_popup()
        idle = ctx.attention.input_idle_seconds()
        if idle is not None and idle >= IDLE_SUPPRESS_SECONDS:
            return  # went idle while asking: the away flow owns that time, no record
        engine = ctx.engine
        if engine.session_id != p.session_id or not engine.is_active:
            return
        self._record(p.session_id, "missed", None)
        self._planner.record("missed")
        self._misses += 1
        if self._misses == 1:
            self._first_miss_epoch = p.fired_epoch
        self.answered.emit("missed")
        if self._misses >= 2 and str(engine.state) == "focus":
            at = self._first_miss_epoch if self._first_miss_epoch is not None else p.fired_epoch
            self._misses = 0
            self._first_miss_epoch = None
            self._miss_pause_sid = p.session_id
            log.info("Two check-ins missed in a row; pausing the session retroactively")
            engine.pause(at=at)
            self.auto_paused.emit()

    # ================================================================== records
    def _record(self, session_id: int, response: str, reason: str | None) -> None:
        ctx = self._ctx
        try:
            ctx.repo.add_checkin(session_id, ctx.clock.now().replace(microsecond=0), response, reason)
        except (sqlite3.Error, ValueError):
            log.exception("Could not record a check-in")
            return
        ctx.events.data_changed.emit("checkins")

    # ================================================================== prompt teardown
    def _drop_prompt(self, notify: bool = True) -> None:
        """Close the open prompt without recording anything."""
        p = self._prompt
        self._prompt = None
        if p is None:
            return
        if p.mode == "inline":
            if notify:
                self.prompt_closed.emit()
        else:
            self._close_popup()

    def _close_popup(self) -> None:
        """Dismiss the popup unless it is playing its confirmation (it closes itself then)."""
        popup = self._popup
        if popup is None:
            return
        try:
            if popup.phase() == "confirm":
                return
            self._popup = None
            popup.close_prompt()
        except RuntimeError:  # already deleted
            self._popup = None

    def _on_popup_closed(self, popup: CheckInPopup) -> None:
        if self._popup is popup:
            self._popup = None
            p = self._prompt
            if p is not None and p.mode == "popup":
                # Closed from outside (e.g. the stack was reset) while unanswered: no record.
                self._prompt = None
        popup.deleteLater()

    # ================================================================== engine / settings
    def _enabled(self) -> bool:
        return bool(self._ctx.settings.get("checkin_enabled"))

    def _sync_session(self) -> None:
        if self._ctx.engine.session_id != self._sid:
            self._adopt_session()

    def _adopt_session(self) -> None:
        engine = self._ctx.engine
        self._sid = engine.session_id
        self._planner.reset(engine.focused_seconds if engine.is_active else 0.0)
        self._misses = 0
        self._first_miss_epoch = None
        self._set_deferred(False)

    def _on_session_started(self, sid: int) -> None:
        self._drop_prompt()
        self._miss_pause_sid = None
        self._sid = int(sid)
        self._planner.reset(0.0)
        self._misses = 0
        self._first_miss_epoch = None
        self._set_deferred(False)

    def _on_state_changed(self, state: str) -> None:
        state = str(state)
        if state != "focus":
            self._drop_prompt()
        if state != "paused":
            self._miss_pause_sid = None
        if state in ("idle", "break"):
            self._sid = None
            self._misses = 0
            self._first_miss_epoch = None
            self._set_deferred(False)
        elif state == "focus":
            self.evaluate()

    def _on_setting(self, key: str) -> None:
        s = self._ctx.settings
        if key in ("checkin_interval_minutes", "checkin_adaptive"):
            self._planner.configure(int(s.get("checkin_interval_minutes")), bool(s.get("checkin_adaptive")))
        elif key == "checkin_enabled" and not s.get("checkin_enabled"):
            self._drop_prompt()
            self._set_deferred(False)

    def _on_attention(self, state: str) -> None:
        if self._deferred and str(state) not in _DEFER_STATES:
            self.evaluate()
