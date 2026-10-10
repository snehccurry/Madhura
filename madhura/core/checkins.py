"""Check-in scheduling on *focused* time (spec §1.5, §3.3). Pure logic, no Qt."""

from __future__ import annotations

__all__ = ["CheckInPlanner", "MIN_REMAINING_SECONDS"]

#: No check-in fires when less than this much session time remains.
MIN_REMAINING_SECONDS = 60.0

_STREAK_1_5X = 3
_STREAK_2X = 6


class CheckInPlanner:
    """Decides when the next check-in is due, measured in focused seconds.

    Adaptive spacing: after 3 consecutive "focused" answers the interval is base × 1.5, after 6
    base × 2 (cap); any "drifted"/"missed" resets it to base. ``record()`` re-schedules the
    pending check-in relative to the last firing, so the answer that completes a streak already
    stretches the very next interval.
    """

    def __init__(self, interval_minutes: int, adaptive: bool = True) -> None:
        self._base = max(1.0, float(interval_minutes)) * 60.0
        self._adaptive = bool(adaptive)
        self._streak = 0
        self._last_fired: float | None = None
        self._next_due = self._base

    # ------------------------------------------------------------------ config

    def configure(self, interval_minutes: int, adaptive: bool) -> None:
        """Change base interval / adaptivity mid-session (keeps the streak, re-plans the next due)."""
        anchor = self._last_fired if self._last_fired is not None else self._next_due - self.current_interval
        self._base = max(1.0, float(interval_minutes)) * 60.0
        self._adaptive = bool(adaptive)
        self._next_due = max(0.0, anchor) + self.current_interval

    def reset(self, focused_seconds: float = 0.0) -> None:
        """Start a new session: streak 0, next due = focused + base."""
        self._streak = 0
        self._last_fired = None
        self._next_due = float(focused_seconds) + self._base

    # ------------------------------------------------------------------ state

    @property
    def base_interval(self) -> float:
        """Base interval in seconds."""
        return self._base

    @property
    def adaptive(self) -> bool:
        """Whether adaptive spacing is on."""
        return self._adaptive

    @property
    def streak(self) -> int:
        """Consecutive "focused" answers."""
        return self._streak

    @property
    def next_due(self) -> float:
        """Focused-seconds threshold of the next check-in."""
        return self._next_due

    @property
    def current_interval(self) -> float:
        """Current interval in seconds (base, ×1.5 or ×2 depending on the focused streak)."""
        if not self._adaptive or self._streak < _STREAK_1_5X:
            return self._base
        if self._streak < _STREAK_2X:
            return self._base * 1.5
        return self._base * 2.0

    # ------------------------------------------------------------------ logic

    def is_due(self, focused_seconds: float, remaining_seconds: float) -> bool:
        """True when focused time reached next_due and at least 60 s of the session remain."""
        if remaining_seconds < MIN_REMAINING_SECONDS:
            return False
        return float(focused_seconds) >= self._next_due

    def fired(self, focused_seconds: float) -> None:
        """A check-in was shown (or dropped): schedule the next one = focused + current interval."""
        self._last_fired = float(focused_seconds)
        self._next_due = self._last_fired + self.current_interval

    def record(self, response: str) -> None:
        """Record an answer: "focused" extends the streak; "drifted"/"missed" reset it."""
        if str(response) == "focused":
            self._streak += 1
        else:
            self._streak = 0
        if self._last_fired is not None:
            self._next_due = self._last_fired + self.current_interval

    def seconds_until_next(self, focused_seconds: float) -> float:
        """Focused seconds until the next check-in (≥ 0)."""
        return max(0.0, self._next_due - float(focused_seconds))
