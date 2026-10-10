"""OS-level user attention state (presentation / fullscreen / locked)."""

from __future__ import annotations

import logging
import sys

__all__ = ["system_attention", "attention_from_quns", "AVAILABLE", "BUSY", "AWAY"]

log = logging.getLogger(__name__)

AVAILABLE = "available"
BUSY = "busy"
AWAY = "away"

# QUERY_USER_NOTIFICATION_STATE values (shellapi.h)
QUNS_NOT_PRESENT = 1  # screen saver, locked, fast user switching
QUNS_BUSY = 2  # fullscreen app
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6  # first hour after a new user signs in (Windows 8+)
QUNS_APP = 7  # immersive (Store) app fullscreen

_MAP = {
    QUNS_NOT_PRESENT: AWAY,
    QUNS_BUSY: BUSY,
    QUNS_RUNNING_D3D_FULL_SCREEN: BUSY,
    QUNS_PRESENTATION_MODE: BUSY,
    QUNS_ACCEPTS_NOTIFICATIONS: AVAILABLE,
    QUNS_QUIET_TIME: AVAILABLE,
    QUNS_APP: BUSY,
}


def attention_from_quns(value: int) -> str:
    """Map a SHQueryUserNotificationState value to "available" | "busy" | "away"."""
    return _MAP.get(int(value), AVAILABLE)


def system_attention() -> str:
    """"available" | "busy" | "away" (Windows SHQueryUserNotificationState; "available" elsewhere)."""
    if sys.platform != "win32":
        return AVAILABLE
    try:
        import ctypes

        state = ctypes.c_int(0)
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:
            return AVAILABLE
        return attention_from_quns(state.value)
    except Exception:  # noqa: BLE001 - platform probing must never raise
        log.debug("SHQueryUserNotificationState failed", exc_info=True)
        return AVAILABLE
