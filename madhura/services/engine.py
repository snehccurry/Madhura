"""FocusEngine — the session/break state machine (spec §3).

All duration math uses ``clock.time()`` epoch deltas; ``clock.now()`` is only used for the ISO
timestamps stored in the database. Every transition (and a 30 s heartbeat) persists a v1 snapshot
plus the session row in ONE transaction so a crash, kill or power loss never loses more than 30 s
and never credits time that wasn't focus.

Re-entrancy: commands may be called from slots connected to any engine signal (e.g.
``start_break()`` inside a ``session_finished`` handler). Each transition bumps a generation
counter; an outer emission sequence stops as soon as a nested command has taken over, and
``state_changed`` is emitted only when (state, session) differs from what listeners last saw.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from madhura.core.clock import Clock
from madhura.core.days import day_bounds, logical_date
from madhura.core.models import Outcome, ValueEnum

__all__ = [
    "EngineState",
    "FocusEngine",
    "SNAPSHOT_KEY",
    "SNAPSHOT_VERSION",
    "MAX_SESSION_MINUTES",
    "MAX_BREAK_MINUTES",
    "MIN_SAVED_FOCUS_SECONDS",
    "SUSPEND_GAP_SECONDS",
    "RESTORE_RESUME_GAP_SECONDS",
    "HEARTBEAT_SECONDS",
    "DEFAULT_BREAK_LABEL",
]

log = logging.getLogger(__name__)

SNAPSHOT_KEY = "engine_snapshot"
SNAPSHOT_VERSION = 1
MAX_SESSION_MINUTES = 480
MAX_BREAK_MINUTES = 240
MIN_SAVED_FOCUS_SECONDS = 60.0
SUSPEND_GAP_SECONDS = 90.0
RESTORE_RESUME_GAP_SECONDS = 120.0
HEARTBEAT_SECONDS = 30.0
COMPLETE_TOLERANCE_SECONDS = 1.0
DEFAULT_BREAK_LABEL = "Break"
_TIMER_SLACK_MS = 20
_EPS = 1e-6


class EngineState(ValueEnum):
    """Engine state; compares equal to its string value."""

    IDLE = "idle"
    FOCUS = "focus"
    PAUSED = "paused"
    BREAK = "break"


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _opt_num(value: Any) -> bool:
    return value is None or _num(value)


class FocusEngine(QObject):
    """Runs focus sessions and breaks; see module docstring and spec §3 for the contract."""

    state_changed = Signal(str)
    tick = Signal()
    session_started = Signal(int)
    session_finished = Signal(int, str, bool)
    session_extended = Signal(int)
    break_started = Signal(int)
    break_finished = Signal(bool)
    away_detected = Signal(int)

    def __init__(self, repo: Any, settings_store: Any, clock: Clock, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._settings_store = settings_store
        self._clock = clock
        self._state = EngineState.IDLE
        self._reset_session_fields()
        self._reset_break_fields()
        self._gen = 0
        self._emitted: tuple[EngineState, int | None] = (EngineState.IDLE, None)
        self._tick_key: tuple[Any, ...] | None = None
        now = clock.time()
        self._last_poll = now
        self._last_heartbeat = now
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.poll)
        self._away_probe: Callable[[], float | None] | None = None

    def set_away_probe(self, probe: Callable[[], float | None] | None) -> None:
        """Install a callable returning the epoch since which the user has been away from the
        computer (input idle), or None (``AwayService`` provides it where idle time is known).

        It is consulted synchronously so away time is never credited: a session that reaches its
        end while the user is away pauses where they left instead of completing, and a
        sleep/suspend gap closes the focus segment there instead of at the last poll (both emit
        ``away_detected`` with the whole time away).
        """
        self._away_probe = probe

    def _away_since(self, limit: float) -> float | None:
        """The away probe's epoch clamped to the running segment; None unless it is before *limit*."""
        probe = self._away_probe
        if probe is None or self._segment_start is None:
            return None
        try:
            at = probe()
        except Exception:  # a broken probe must never stop the engine
            log.exception("away probe failed")
            return None
        if not _num(at):
            return None
        at = max(float(at), self._segment_start)  # type: ignore[arg-type]
        return at if at < limit else None

    # ================================================================== fields

    def _reset_session_fields(self) -> None:
        self._session_id: int | None = None
        self._task = ""
        self._project: str | None = None
        self._planned_task_id: int | None = None
        self._planned = 0
        self._started_at: datetime | None = None
        self._focused_before = 0.0
        self._segment_start: float | None = None
        self._paused_before = 0.0
        self._pause_start: float | None = None

    def _reset_break_fields(self) -> None:
        self._break_total = 0
        self._break_start: float | None = None
        self._break_label: str | None = None

    def _cfg(self) -> Any:
        return getattr(self._settings_store, "settings", self._settings_store)

    # ================================================================== properties

    @property
    def state(self) -> EngineState:
        """Current state."""
        return self._state

    @property
    def is_active(self) -> bool:
        """True while a session exists (FOCUS or PAUSED)."""
        return self._state in (EngineState.FOCUS, EngineState.PAUSED)

    @property
    def session_id(self) -> int | None:
        """Id of the current session row (FOCUS/PAUSED)."""
        return self._session_id

    @property
    def task(self) -> str:
        """Current task title ("" when idle)."""
        return self._task

    @property
    def project(self) -> str | None:
        """Current project."""
        return self._project

    @property
    def planned_task_id(self) -> int | None:
        """Up next task the session was started from."""
        return self._planned_task_id

    @property
    def planned_seconds(self) -> int:
        """Planned length (including extensions)."""
        return self._planned

    def _focused_at(self, now: float) -> float:
        focused = self._focused_before
        if self._state is EngineState.FOCUS and self._segment_start is not None:
            focused += max(0.0, now - self._segment_start)
        return min(max(0.0, focused), float(self._planned))

    def _paused_at(self, now: float) -> float:
        paused = self._paused_before
        if self._state is EngineState.PAUSED and self._pause_start is not None:
            paused += max(0.0, now - self._pause_start)
        return max(0.0, paused)

    @property
    def focused_seconds(self) -> float:
        """Focused time so far (0..planned)."""
        return self._focused_at(self._clock.time()) if self.is_active else 0.0

    @property
    def paused_seconds(self) -> float:
        """Paused (and discarded) time so far."""
        return self._paused_at(self._clock.time()) if self.is_active else 0.0

    @property
    def remaining_seconds(self) -> float:
        """Planned − focused (≥ 0)."""
        if not self.is_active:
            return 0.0
        return max(0.0, self._planned - self._focused_at(self._clock.time()))

    @property
    def remaining_whole_seconds(self) -> int:
        """ceil(remaining_seconds) — the value the timer displays."""
        return max(0, int(math.ceil(self.remaining_seconds - _EPS)))

    @property
    def progress(self) -> float:
        """Elapsed fraction 0..1 of the planned time."""
        if not self.is_active or self._planned <= 0:
            return 0.0
        return min(1.0, max(0.0, self.focused_seconds / self._planned))

    @property
    def ends_at(self) -> datetime | None:
        """Projected end (now + remaining) while FOCUS/PAUSED."""
        if not self.is_active:
            return None
        return self._clock.now() + timedelta(seconds=self.remaining_seconds)

    @property
    def paused_since(self) -> datetime | None:
        """When the current pause began (PAUSED only)."""
        if self._state is not EngineState.PAUSED or self._pause_start is None:
            return None
        return self._clock.now() - timedelta(seconds=max(0.0, self._clock.time() - self._pause_start))

    @property
    def started_at(self) -> datetime | None:
        """Session start (naive local) while FOCUS/PAUSED."""
        return self._started_at if self.is_active else None

    @property
    def break_label(self) -> str | None:
        """Label of the running break ("Break", "Lunch", ...)."""
        return self._break_label if self._state is EngineState.BREAK else None

    @property
    def break_total_seconds(self) -> int:
        """Length of the running break."""
        return self._break_total if self._state is EngineState.BREAK else 0

    @property
    def break_remaining_seconds(self) -> float:
        """Seconds left in the running break (≥ 0)."""
        if self._state is not EngineState.BREAK or self._break_start is None:
            return 0.0
        return max(0.0, self._break_start + self._break_total - self._clock.time())

    @property
    def break_remaining_whole_seconds(self) -> int:
        """ceil(break_remaining_seconds)."""
        return max(0, int(math.ceil(self.break_remaining_seconds - _EPS)))

    @property
    def break_progress(self) -> float:
        """Elapsed fraction 0..1 of the running break."""
        if self._state is not EngineState.BREAK or self._break_total <= 0:
            return 0.0
        return min(1.0, max(0.0, 1.0 - self.break_remaining_seconds / self._break_total))

    @property
    def break_ends_at(self) -> datetime | None:
        """Projected end of the running break."""
        if self._state is not EngineState.BREAK:
            return None
        return self._clock.now() + timedelta(seconds=self.break_remaining_seconds)

    @property
    def completed_sessions_today(self) -> int:
        """Completed sessions started on the current logical day."""
        hour = int(getattr(self._cfg(), "day_start_hour", 4))
        start, end = day_bounds(logical_date(self._clock.now(), hour), hour)
        try:
            return len(self._repo.sessions_between(start, end, outcomes=(Outcome.COMPLETED.value,)))
        except sqlite3.Error:
            log.exception("Could not count today's sessions")
            return 0

    # ================================================================== persistence

    def _snapshot(self, now: float) -> dict[str, Any] | None:
        if self._state is EngineState.IDLE:
            return None
        return {
            "v": SNAPSHOT_VERSION,
            "state": self._state.value,
            "session_id": self._session_id,
            "task": self._task,
            "project": self._project,
            "planned_task_id": self._planned_task_id,
            "planned_seconds": self._planned,
            "focused_before": self._focused_before,
            "segment_start": self._segment_start,
            "paused_before": self._paused_before,
            "pause_start": self._pause_start,
            "break_total": self._break_total,
            "break_start": self._break_start,
            "break_label": self._break_label,
            "last_alive": now,
        }

    def _write_checkpoint(self, now: float) -> None:
        snapshot = self._snapshot(now)
        if self.is_active and self._session_id is not None:
            self._repo.save_engine_checkpoint(
                snapshot,
                self._session_id,
                focused_seconds=self._focused_at(now),
                paused_seconds=self._paused_at(now),
                planned_seconds=self._planned,
            )
        else:
            self._repo.save_engine_checkpoint(snapshot, None)
        self._last_heartbeat = now

    def _persist(self, now: float | None = None) -> None:
        if now is None:
            now = self._clock.time()
        try:
            self._write_checkpoint(now)
        except sqlite3.Error:
            log.exception("Engine checkpoint failed")

    def checkpoint(self) -> None:
        """Write the snapshot + session row now (quit / commitDataRequest)."""
        self._persist()

    # ================================================================== emission helpers

    def _bump(self) -> int:
        self._gen += 1
        return self._gen

    def _emit_state(self) -> None:
        key = (self._state, self._session_id)
        if key != self._emitted:
            self._emitted = key
            self.state_changed.emit(self._state.value)

    def _current_tick_key(self) -> tuple[Any, ...] | None:
        if self.is_active:
            return ("s", self._session_id, self.remaining_whole_seconds)
        if self._state is EngineState.BREAK:
            return ("b", self._break_start, self.break_remaining_whole_seconds)
        return None

    def _emit_tick(self) -> None:
        key = self._current_tick_key()
        if key == self._tick_key:
            return
        self._tick_key = key
        if key is not None:
            self.tick.emit()

    def _arm(self) -> None:
        state = self._state
        if state is EngineState.IDLE:
            self._timer.stop()
            return
        now = self._clock.time()
        if state is EngineState.PAUSED:
            wait = self._last_heartbeat + HEARTBEAT_SECONDS - now
            self._timer.start(int(min(HEARTBEAT_SECONDS, max(1.0, wait)) * 1000))
            return
        if state is EngineState.FOCUS:
            seg = self._segment_start if self._segment_start is not None else now
            remaining = self._planned - (self._focused_before + now - seg)
        else:
            start = self._break_start if self._break_start is not None else now
            remaining = start + self._break_total - now
        if remaining <= 0:
            self._timer.start(0)
            return
        frac = remaining - math.floor(remaining)
        until_change = frac if frac > _EPS else 1.0
        self._timer.start(min(1000, int(until_change * 1000) + _TIMER_SLACK_MS))

    def stop_timer(self) -> None:
        """Stop the internal timer (tests / shutdown). poll() keeps working."""
        self._timer.stop()

    # ================================================================== commands

    def start(
        self,
        task: str,
        minutes: float | None,
        project: str | None = None,
        planned_task_id: int | None = None,
    ) -> int | None:
        """Start a focus session (valid in IDLE and BREAK). Returns the session id or None."""
        title = (task or "").strip()
        if not title:
            log.info("start() ignored: empty task")
            return None
        if self.is_active:
            log.warning("start() ignored: a session is already %s", self._state.value)
            return None
        if self._state is EngineState.BREAK:
            gen = self._end_break(natural=False)
            if self._gen != gen or self._state is not EngineState.IDLE:
                return None
        if minutes is None:
            minutes = getattr(self._cfg(), "default_minutes", 60)
        try:
            minutes_f = float(minutes)
        except (TypeError, ValueError):
            log.warning("start() ignored: bad minutes %r", minutes)
            self._emit_state()
            return None
        minutes_f = min(float(MAX_SESSION_MINUTES), max(1.0, minutes_f))
        planned = int(round(minutes_f * 60))
        proj = (project or "").strip() or None
        now = self._clock.time()
        started = self._clock.now().replace(microsecond=0)
        try:
            with self._repo.transaction():
                sid = self._repo.create_session(title, proj, planned_task_id, started, planned)
                self._reset_session_fields()
                self._reset_break_fields()
                self._session_id = sid
                self._task = title
                self._project = proj
                self._planned_task_id = planned_task_id
                self._planned = planned
                self._started_at = started
                self._segment_start = now
                self._state = EngineState.FOCUS
                self._write_checkpoint(now)
        except sqlite3.Error:
            log.exception("Could not start a session")
            self._reset_session_fields()
            self._state = EngineState.IDLE
            self._emit_state()
            return None
        gen = self._bump()
        self._last_poll = now
        self._arm()
        self.session_started.emit(sid)
        if self._gen != gen:
            return sid
        self._emit_state()
        if self._gen != gen:
            return sid
        self._emit_tick()
        return sid

    def pause(self, at: float | None = None) -> None:
        """Pause FOCUS. *at* (epoch) makes the pause retroactive, clamped to the segment start."""
        if self._state is not EngineState.FOCUS or self._segment_start is None:
            return
        now = self._clock.time()
        when = now if at is None else min(float(at), now)
        when = max(when, self._segment_start)
        self._focused_before = min(float(self._planned), self._focused_before + (when - self._segment_start))
        self._segment_start = None
        self._pause_start = when
        self._state = EngineState.PAUSED
        self._persist(now)
        gen = self._bump()
        self._last_poll = now
        self._arm()
        self._emit_state()
        if self._gen == gen:
            self._emit_tick()

    def resume(self) -> None:
        """Resume a PAUSED session."""
        if self._state is not EngineState.PAUSED:
            return
        now = self._clock.time()
        if self._pause_start is not None:
            self._paused_before += max(0.0, now - self._pause_start)
        self._pause_start = None
        self._segment_start = now
        self._state = EngineState.FOCUS
        self._persist(now)
        gen = self._bump()
        self._last_poll = now
        self._arm()
        self._emit_state()
        if self._gen == gen:
            self._emit_tick()

    def toggle_pause(self) -> None:
        """Pause when focusing, resume when paused."""
        if self._state is EngineState.FOCUS:
            self.pause()
        elif self._state is EngineState.PAUSED:
            self.resume()

    def extend(self, minutes: float) -> int:
        """Add minutes to the session (FOCUS/PAUSED; total capped at 480 min). Returns seconds added."""
        if not self.is_active:
            return 0
        try:
            add = int(round(float(minutes) * 60))
        except (TypeError, ValueError):
            return 0
        add = min(add, MAX_SESSION_MINUTES * 60 - self._planned)
        if add <= 0:
            return 0
        self._planned += add
        now = self._clock.time()
        self._persist(now)
        self._arm()
        gen = self._gen
        self.session_extended.emit(add)
        if self._gen == gen:
            self._emit_tick()
        return add

    def discard_focused(self, seconds: float) -> float:
        """Remove *seconds* of focused time (clamped ≥ 0); they count as paused. Returns seconds removed."""
        if not self.is_active:
            return 0.0
        now = self._clock.time()
        current = self._focused_at(now)
        removed = min(current, max(0.0, float(seconds)))
        if removed <= 0:
            return 0.0
        self._focused_before = current - removed
        if self._state is EngineState.FOCUS:
            self._segment_start = now
        self._paused_before += removed
        self._persist(now)
        self._arm()
        self._emit_tick()
        return removed

    def finish(self) -> str | None:
        """End now: < 60 s focused → abandoned (row deleted); ≤ 1 s left → completed; else ended_early."""
        if not self.is_active:
            return None
        now = self._clock.time()
        focused = self._focused_at(now)
        if focused < MIN_SAVED_FOCUS_SECONDS:
            return self._end_session(Outcome.ABANDONED, focused, now)
        if self._planned - focused <= COMPLETE_TOLERANCE_SECONDS:
            return self._end_session(Outcome.COMPLETED, float(self._planned), now)
        return self._end_session(Outcome.ENDED_EARLY, focused, now)

    def abandon(self) -> str | None:
        """Discard the session: row deleted when < 60 s focused, else stored as 'abandoned'."""
        if not self.is_active:
            return None
        now = self._clock.time()
        return self._end_session(Outcome.ABANDONED, self._focused_at(now), now)

    def start_break(self, minutes: float | None = None, label: str | None = None) -> bool:
        """Start a break (IDLE; replaces a running break). Default length settings.break_minutes."""
        if self.is_active:
            log.warning("start_break() ignored during a session")
            return False
        if minutes is None:
            minutes = getattr(self._cfg(), "break_minutes", 5)
        try:
            minutes_f = float(minutes)
        except (TypeError, ValueError):
            return False
        minutes_f = min(float(MAX_BREAK_MINUTES), max(1.0, minutes_f))
        now = self._clock.time()
        self._reset_session_fields()
        self._break_total = int(round(minutes_f * 60))
        self._break_start = now
        self._break_label = (label or "").strip() or DEFAULT_BREAK_LABEL
        self._state = EngineState.BREAK
        self._persist(now)
        gen = self._bump()
        self._last_poll = now
        self._arm()
        self.break_started.emit(self._break_total)
        if self._gen != gen:
            return True
        self._emit_state()
        if self._gen == gen:
            self._emit_tick()
        return True

    def skip_break(self) -> None:
        """End the running break early → break_finished(False) → IDLE."""
        if self._state is not EngineState.BREAK:
            return
        gen = self._end_break(natural=False)
        if self._gen == gen:
            self._emit_state()
            self._emit_tick()

    # ================================================================== internal transitions

    def _end_break(self, natural: bool) -> int:
        """Break → IDLE, persist, emit break_finished(natural). Returns the generation."""
        now = self._clock.time()
        self._reset_break_fields()
        self._state = EngineState.IDLE
        self._persist(now)
        gen = self._bump()
        self._last_poll = now
        self._arm()
        self.break_finished.emit(bool(natural))
        return gen

    def _end_session(
        self,
        outcome: Outcome,
        focused: float,
        now: float,
        ended_at: datetime | None = None,
        restored: bool = False,
        auto_break: bool = False,
    ) -> str:
        sid = self._session_id
        planned = self._planned
        paused = self._paused_at(now)
        ended = (ended_at or self._clock.now()).replace(microsecond=0)
        delete = outcome is Outcome.ABANDONED and focused < MIN_SAVED_FOCUS_SECONDS
        break_total = 0
        if auto_break:
            minutes = float(getattr(self._cfg(), "break_minutes", 5))
            break_total = int(round(min(float(MAX_BREAK_MINUTES), max(1.0, minutes)) * 60))

        self._reset_session_fields()
        self._reset_break_fields()
        if auto_break:
            self._break_total = break_total
            self._break_start = now
            self._break_label = DEFAULT_BREAK_LABEL
            self._state = EngineState.BREAK
        else:
            self._state = EngineState.IDLE
        try:
            with self._repo.transaction():
                if sid is not None:
                    if delete:
                        self._repo.delete_session(sid)
                    else:
                        self._repo.update_session(
                            sid,
                            ended_at=ended,
                            focused_seconds=focused,
                            paused_seconds=paused,
                            planned_seconds=planned,
                            outcome=outcome,
                        )
                self._write_checkpoint(now)
        except sqlite3.Error:
            log.exception("Could not save the session outcome")

        gen = self._bump()
        self._last_poll = now
        self._arm()
        if sid is not None:
            self.session_finished.emit(sid, outcome.value, bool(restored))
            if self._gen != gen:
                return outcome.value
        if auto_break:
            self.break_started.emit(break_total)
            if self._gen != gen:
                return outcome.value
        self._emit_state()
        if self._gen == gen:
            self._emit_tick()
        return outcome.value

    def _complete_naturally(self, now: float) -> None:
        seg = self._segment_start if self._segment_start is not None else now
        completion = seg + (self._planned - self._focused_before)
        overshoot = max(0.0, now - completion)
        ended = self._clock.now() - timedelta(seconds=overshoot)
        cfg = self._cfg()
        auto = bool(getattr(cfg, "breaks_enabled", True)) and bool(getattr(cfg, "auto_start_break", False))
        self._end_session(Outcome.COMPLETED, float(self._planned), now, ended_at=ended, auto_break=auto)

    def _suspend_to_paused(self, at: float, gap: float, now: float) -> None:
        seg = self._segment_start if self._segment_start is not None else at
        self._focused_before = min(float(self._planned), self._focused_before + max(0.0, at - seg))
        self._segment_start = None
        self._pause_start = at
        self._state = EngineState.PAUSED
        self._persist(now)
        gen = self._bump()
        self._last_poll = now
        self._arm()
        log.info("Away for %.0f s during focus; session paused", gap)
        self._emit_state()
        if self._gen != gen:
            return
        self.away_detected.emit(int(round(gap)))
        if self._gen == gen:
            self._emit_tick()

    def _shift_anchors(self, delta: float) -> None:
        if self._segment_start is not None:
            self._segment_start += delta
        if self._pause_start is not None:
            self._pause_start += delta
        if self._break_start is not None:
            self._break_start += delta
        self._last_heartbeat += delta

    # ================================================================== poll

    def poll(self) -> None:
        """Advance the state machine to clock.time(); the timer calls this, tests call it directly."""
        now = self._clock.time()
        dt = now - self._last_poll
        state = self._state
        if state is EngineState.IDLE:
            self._last_poll = now
            self._arm()
            return

        if dt < 0:
            log.info("Clock moved back by %.0f s; re-anchoring", -dt)
            self._shift_anchors(dt)
            self._last_poll = now
            self._persist(now)
        elif dt > SUSPEND_GAP_SECONDS:
            if state is EngineState.FOCUS:
                at = self._away_since(self._last_poll)
                at = self._last_poll if at is None else at
                self._suspend_to_paused(at, now - at, now)
                return
            if state is EngineState.BREAK and self._break_start is not None:
                if now >= self._break_start + self._break_total:
                    gen = self._end_break(natural=False)
                    if self._gen == gen:
                        self._emit_state()
                        self._emit_tick()
                    return
            self._last_poll = now
        else:
            self._last_poll = now

        if state is EngineState.FOCUS and self._segment_start is not None:
            if self._focused_before + (now - self._segment_start) >= self._planned:
                away = self._away_since(self._segment_start + self._planned - self._focused_before)
                if away is not None:
                    # Reached the end while the user is away: pause where they left, credit nothing more.
                    self._suspend_to_paused(away, now - away, now)
                    return
                self._complete_naturally(now)
                return
        elif state is EngineState.BREAK and self._break_start is not None:
            if now >= self._break_start + self._break_total:
                gen = self._end_break(natural=True)
                if self._gen == gen:
                    self._emit_state()
                    self._emit_tick()
                return

        if now - self._last_heartbeat >= HEARTBEAT_SECONDS:
            self._persist(now)
        gen = self._gen
        self._emit_tick()
        if self._gen == gen:
            self._arm()

    # ================================================================== restore

    @staticmethod
    def _valid_snapshot(snap: Any) -> dict[str, Any] | None:
        if not isinstance(snap, dict) or snap.get("v") != SNAPSHOT_VERSION:
            return None
        try:
            state = EngineState(snap.get("state"))
        except ValueError:
            return None
        if not _num(snap.get("last_alive")):
            return None
        for key in ("focused_before", "paused_before"):
            if not _num(snap.get(key, 0)):
                return None
        for key in ("segment_start", "pause_start", "break_start"):
            if not _opt_num(snap.get(key)):
                return None
        if state in (EngineState.FOCUS, EngineState.PAUSED):
            if not isinstance(snap.get("session_id"), int) or not _num(snap.get("planned_seconds")):
                return None
            if snap["planned_seconds"] <= 0:
                return None
            if state is EngineState.FOCUS and snap.get("segment_start") is None:
                return None
            if state is EngineState.PAUSED and snap.get("pause_start") is None:
                return None
        elif state is EngineState.BREAK:
            if snap.get("break_start") is None or not _num(snap.get("break_total")) or snap["break_total"] <= 0:
                return None
        return snap

    def _close_orphans(self, keep_id: int | None) -> None:
        for row in self._repo.active_sessions():
            if row.id == keep_id:
                continue
            if row.focused_seconds < MIN_SAVED_FOCUS_SECONDS:
                self._repo.delete_session(row.id)
                log.info("Deleted orphaned short session %s", row.id)
            else:
                self._repo.update_session(
                    row.id,
                    ended_at=row.started_at + timedelta(seconds=row.focused_seconds),
                    outcome=Outcome.ENDED_EARLY,
                )
                log.info("Closed orphaned session %s as ended_early", row.id)

    def restore(self) -> None:
        """Resume from the persisted snapshot (call once at startup after services are connected)."""
        if self._state is not EngineState.IDLE:
            log.warning("restore() ignored: engine already %s", self._state.value)
            return
        try:
            snap = self._valid_snapshot(self._repo.kv_get(SNAPSHOT_KEY))
            keep_id: int | None = None
            started_at: datetime | None = None
            if snap is not None and snap["state"] in (EngineState.FOCUS.value, EngineState.PAUSED.value):
                row = self._repo.get_session(snap["session_id"])
                if row is None or row.outcome is not Outcome.ACTIVE:
                    snap = None
                else:
                    keep_id = row.id
                    started_at = row.started_at
            with self._repo.transaction():
                self._close_orphans(keep_id)
                if snap is None:
                    self._repo.kv_set(SNAPSHOT_KEY, None)
        except sqlite3.Error:
            log.exception("Engine restore failed")
            return
        now = self._clock.time()
        self._last_poll = now
        if snap is None:
            self._arm()
            return

        state = EngineState(snap["state"])
        last_alive = float(snap["last_alive"])
        gap = now - last_alive
        self._segment_start = snap.get("segment_start")
        self._pause_start = snap.get("pause_start")
        self._break_start = snap.get("break_start")
        if gap < 0:
            self._shift_anchors(gap)
            last_alive = now
            gap = 0.0

        if state is EngineState.BREAK:
            self._break_total = int(snap["break_total"])
            self._break_label = snap.get("break_label") or DEFAULT_BREAK_LABEL
            self._segment_start = self._pause_start = None
            if self._break_start is not None and now < self._break_start + self._break_total:
                self._state = EngineState.BREAK
                self._persist(now)
                self._bump()
                self._arm()
                self._emit_state()
                self._emit_tick()
            else:
                self._reset_break_fields()
                self._persist(now)
                self._arm()
            return

        self._session_id = int(snap["session_id"])
        self._task = str(snap.get("task") or "")
        self._project = snap.get("project") or None
        self._planned_task_id = snap.get("planned_task_id")
        self._planned = int(snap["planned_seconds"])
        self._started_at = started_at
        self._focused_before = float(snap.get("focused_before") or 0.0)
        self._paused_before = float(snap.get("paused_before") or 0.0)
        self._break_start = None

        if state is EngineState.PAUSED:
            self._segment_start = None
            self._state = EngineState.PAUSED
            self._persist(now)
            self._bump()
            self._arm()
            self._emit_state()
            self._emit_tick()
            return

        seg = float(self._segment_start if self._segment_start is not None else last_alive)
        focused_at_death = self._focused_before + max(0.0, last_alive - seg)
        self._state = EngineState.FOCUS
        if focused_at_death >= self._planned:
            completion = seg + (self._planned - self._focused_before)
            ended = self._clock.now() - timedelta(seconds=max(0.0, now - completion))
            self._end_session(Outcome.COMPLETED, float(self._planned), now, ended_at=ended, restored=True)
            return
        if gap <= RESTORE_RESUME_GAP_SECONDS:
            self._persist(now)
            self._bump()
            self._arm()
            self._emit_state()
            self._emit_tick()
            return
        self._focused_before = min(float(self._planned), focused_at_death)
        self._segment_start = None
        self._pause_start = last_alive
        self._state = EngineState.PAUSED
        self._persist(now)
        gen = self._bump()
        self._arm()
        self._emit_state()
        if self._gen != gen:
            return
        self.away_detected.emit(int(round(gap)))
        if self._gen == gen:
            self._emit_tick()
