"""Time sources. The ONLY module allowed to read the wall clock (enforced by a test).

* ``now()``  → naive *local* datetime, used only for stored ISO strings and display.
* ``time()`` → epoch seconds (float), used for ALL duration math.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone, tzinfo
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SystemClock", "FakeClock"]


@runtime_checkable
class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Current naive local datetime."""
        ...

    def time(self) -> float:
        """Current epoch seconds."""
        ...


class SystemClock:
    """The real wall clock."""

    def now(self) -> datetime:
        """Current naive local datetime."""
        return datetime.now()

    def time(self) -> float:
        """Current epoch seconds."""
        return _time.time()


class FakeClock:
    """Deterministic clock for tests.

    The epoch counter is the source of truth; ``now()`` is derived from it. With a *tz*
    (e.g. ``zoneinfo.ZoneInfo("America/New_York")``) naive datetimes are interpreted in that zone
    and ``now()`` models DST transitions exactly (``advance(3600)`` across spring-forward jumps
    two wall-clock hours). Without *tz*, naive datetimes are treated as UTC (no DST).
    """

    def __init__(self, start: datetime, tz: tzinfo | None = None) -> None:
        self._tz: tzinfo = tz if tz is not None else timezone.utc
        self._t: float = self._to_epoch(start)

    @property
    def tz(self) -> tzinfo:
        """Time zone used to map epoch seconds to local wall time."""
        return self._tz

    def _to_epoch(self, dt: datetime) -> float:
        aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=self._tz)
        return aware.timestamp()

    def now(self) -> datetime:
        """Naive local datetime for the current fake instant."""
        return datetime.fromtimestamp(self._t, self._tz).replace(tzinfo=None)

    def time(self) -> float:
        """Current fake epoch seconds."""
        return self._t

    def advance(self, seconds: float) -> None:
        """Move the clock by *seconds* (negative values simulate the clock being set back)."""
        self._t += float(seconds)

    def set(self, dt: datetime) -> None:
        """Jump to *dt* (naive = local in this clock's zone); may move backwards."""
        self._t = self._to_epoch(dt)

    def set_time(self, epoch: float) -> None:
        """Jump to an absolute epoch timestamp."""
        self._t = float(epoch)

    def __repr__(self) -> str:
        return f"FakeClock({self.now().isoformat()}, t={self._t:.3f})"

