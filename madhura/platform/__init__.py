"""Platform detection and OS integration helpers (all calls are guarded and never raise)."""

from __future__ import annotations

import sys

__all__ = ["IS_WINDOWS", "IS_MAC", "IS_LINUX"]

IS_WINDOWS: bool = sys.platform == "win32"
IS_MAC: bool = sys.platform == "darwin"
IS_LINUX: bool = sys.platform.startswith("linux")
