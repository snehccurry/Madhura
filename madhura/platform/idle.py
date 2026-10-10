"""System-wide input idle time (spec §2): Windows GetLastInputInfo, macOS CoreGraphics, else None."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

__all__ = ["get_idle_seconds", "idle_ms_from_ticks"]

log = logging.getLogger(__name__)

_U32 = 0xFFFFFFFF


class _Cache:
    """Lazily-resolved native entry points (None = unavailable)."""

    resolved: bool = False
    fn: Callable[[], float | None] | None = None


def idle_ms_from_ticks(now_tick: int, last_input_tick: int) -> int:
    """Milliseconds since last input given two 32-bit tick counts (handles the 49.7-day wrap)."""
    return (int(now_tick) - int(last_input_tick)) & _U32


def _windows_fn() -> Callable[[], float | None] | None:
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32: Any = ctypes.windll.user32
    kernel32: Any = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = wintypes.DWORD

    def _idle() -> float | None:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = int(kernel32.GetTickCount()) & _U32
        return idle_ms_from_ticks(now, info.dwTime) / 1000.0

    return _idle


def _mac_fn() -> Callable[[], float | None] | None:
    import ctypes
    import ctypes.util

    path = ctypes.util.find_library("ApplicationServices") or ctypes.util.find_library("CoreGraphics")
    if not path:
        return None
    lib: Any = ctypes.cdll.LoadLibrary(path)
    fn = lib.CGEventSourceSecondsSinceLastEventType
    fn.restype = ctypes.c_double
    fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
    combined_session_state = 0
    any_input_event = 0xFFFFFFFF

    def _idle() -> float | None:
        value = float(fn(combined_session_state, any_input_event))
        return value if value >= 0 else None

    return _idle


def _resolve() -> Callable[[], float | None] | None:
    if _Cache.resolved:
        return _Cache.fn
    _Cache.resolved = True
    try:
        if sys.platform == "win32":
            _Cache.fn = _windows_fn()
        elif sys.platform == "darwin":
            _Cache.fn = _mac_fn()
    except Exception:  # noqa: BLE001 - platform probing must never raise
        log.debug("Idle-time API unavailable", exc_info=True)
        _Cache.fn = None
    return _Cache.fn


def get_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input, or None when the platform can't tell."""
    fn = _resolve()
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001
        log.debug("Idle-time query failed", exc_info=True)
        return None
