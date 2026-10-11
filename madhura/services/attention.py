"""AttentionMonitor — is now a good moment to interrupt? (spec §1.5, §1.6, §1.7)

State, in priority order:
    "quiet"     user asked for quiet (kv 'quiet_until' in the future)
    "away"      OS says not present (locked/screensaver) or input idle > 300 s
    "busy"      presenting / fullscreen app
    "available" otherwise
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from madhura.core.clock import Clock
from madhura.core.models import from_iso, to_iso
from madhura.log import warn_once
from madhura.platform import attention as platform_attention
from madhura.platform import idle as platform_idle

__all__ = ["AttentionMonitor", "QUIET_KEY", "AWAY_IDLE_SECONDS", "POLL_MS", "BUSY_POLL_MS"]

log = logging.getLogger(__name__)

QUIET_KEY = "quiet_until"
AWAY_IDLE_SECONDS = 300.0
POLL_MS = 15_000
BUSY_POLL_MS = 2_000

AVAILABLE = "available"
BUSY = "busy"
AWAY = "away"
QUIET = "quiet"


class AttentionMonitor(QObject):
    """Combines OS attention, input idle time and the user's quiet mode; emits ``changed(state)``."""

    changed = Signal(str)

    def __init__(
        self,
        clock: Clock,
        repo: Any,
        parent: QObject | None = None,
        *,
        system_attention_fn: Callable[[], str] | None = None,
        idle_fn: Callable[[], float | None] | None = None,
        autostart: bool = True,
    ) -> None:
        super().__init__(parent)
        self._clock = clock
        self._repo = repo
        self._system_fn = system_attention_fn or platform_attention.system_attention
        self._idle_fn = idle_fn or platform_idle.get_idle_seconds
        self._warned: set[str] = set()
        self._quiet_until: datetime | None = self._load_quiet()
        self._state = AVAILABLE
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        self._state = self._compute()
        if autostart:
            self._schedule()

    # ------------------------------------------------------------------ quiet

    def _load_quiet(self) -> datetime | None:
        try:
            value = from_iso(self._repo.kv_get(QUIET_KEY))
        except (TypeError, ValueError):
            return None
        return value

    @property
    def quiet_until(self) -> datetime | None:
        """End of quiet mode, or None when not quiet."""
        if self._quiet_until is not None and self._quiet_until <= self._clock.now():
            return None
        return self._quiet_until

    def is_quiet(self) -> bool:
        """True while quiet mode is active."""
        return self.quiet_until is not None

    def set_quiet(self, minutes: float) -> None:
        """Be quiet for *minutes* from now (no nudges, check-ins deferred, pill hidden, no sounds)."""
        until = (self._clock.now() + timedelta(minutes=float(minutes))).replace(microsecond=0)
        self._quiet_until = until
        self._repo.kv_set(QUIET_KEY, to_iso(until))
        self.refresh()

    def clear_quiet(self) -> None:
        """End quiet mode now."""
        self._quiet_until = None
        self._repo.kv_set(QUIET_KEY, None)
        self.refresh()

    # ------------------------------------------------------------------ inputs

    def input_idle_seconds(self) -> float | None:
        """Seconds since the last keyboard/mouse input (None when the OS can't tell)."""
        try:
            return self._idle_fn()
        except Exception:  # noqa: BLE001 - platform probing must never raise
            warn_once(log, self._warned, "idle", "Input idle probe failed")
            return None

    def system_state(self) -> str:
        """Raw OS attention ("available" | "busy" | "away")."""
        try:
            return self._system_fn()
        except Exception:  # noqa: BLE001 - platform probing must never raise
            warn_once(log, self._warned, "system", "System attention probe failed")
            return AVAILABLE

    # ------------------------------------------------------------------ state

    def state(self) -> str:
        """Current state: "available" | "busy" | "away" | "quiet" (quiet expiry is checked live)."""
        if self._state == QUIET and self.quiet_until is None:
            return self.refresh()
        return self._state

    def _compute(self) -> str:
        if self.quiet_until is not None:
            return QUIET
        if self._quiet_until is not None:
            # expired → forget it
            self._quiet_until = None
            try:
                self._repo.kv_set(QUIET_KEY, None)
            except Exception:  # noqa: BLE001 - repo may be closing during shutdown
                log.debug("Could not clear expired quiet_until", exc_info=True)
        system = self.system_state()
        if system == AWAY:
            return AWAY
        idle = self.input_idle_seconds()
        if idle is not None and idle > AWAY_IDLE_SECONDS:
            return AWAY
        if system == BUSY:
            return BUSY
        return AVAILABLE

    def refresh(self) -> str:
        """Re-evaluate now; emits ``changed`` when the state differs. Returns the state."""
        new = self._compute()
        if new != self._state:
            self._state = new
            self.changed.emit(new)
        self._schedule()
        return self._state

    def _schedule(self) -> None:
        interval = BUSY_POLL_MS if self._state == BUSY else POLL_MS
        until = self.quiet_until
        if until is not None:
            ms = int((until - self._clock.now()).total_seconds() * 1000) + 50
            interval = max(50, min(interval, ms))
        self._timer.start(interval)

    def stop(self) -> None:
        """Stop polling."""
        self._timer.stop()
