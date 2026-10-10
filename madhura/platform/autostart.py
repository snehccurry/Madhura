"""STUB — owned by [S]. Launch-at-login integration.

Created by the app shell [F3] reporting "unsupported"; the settings agent replaces this module.
"""

from __future__ import annotations

import sys

__all__ = ["is_supported", "is_enabled", "status", "set_enabled", "launch_command"]


def is_supported() -> bool:
    """True when launch-at-login can be configured on this OS. STUB: False."""
    return False


def is_enabled() -> bool:
    """True when Madhura starts at login. STUB: False."""
    return False


def status() -> str:
    """"on" | "off" | "disabled_by_os" | "needs_repair". STUB: "off"."""
    return "off"


def set_enabled(enabled: bool) -> bool:
    """Enable/disable launch at login; returns True on success. STUB: no-op (False)."""
    return False


def launch_command() -> list[str]:
    """Command line used for autostart."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--autostart"]
    return [sys.executable, "-m", "madhura", "--autostart"]
