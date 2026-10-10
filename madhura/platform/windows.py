"""Windows integration via ctypes (spec §7.7). Every function is safe to call on any OS.

On non-Windows platforms the functions fall back to portable Qt behaviour or do nothing.
No function in this module ever raises.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from madhura import APP_ID

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

__all__ = [
    "set_app_user_model_id",
    "set_caption_colors",
    "set_round_corners",
    "set_dark_title_bar",
    "bring_to_front",
    "reassert_topmost",
    "flash",
    "allow_set_foreground",
    "client_animations_enabled",
    "windows_build",
    "is_windows_11",
    "colorref",
]

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# DWM attributes
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

# SetWindowPos
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SPI_GETCLIENTAREAANIMATION = 0x1042
FLASHW_TRAY = 0x00000002
FLASHW_TIMERNOFG = 0x0000000C

_TOPMOST_RELEASE_MS = 1500
_ALERT_MS = 3000


def colorref(color: Any) -> int:
    """Convert a QColor / "#RRGGBB" string / (r, g, b) tuple to a Win32 COLORREF (0x00BBGGRR)."""
    if isinstance(color, str):
        text = color.lstrip("#")
        r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    elif isinstance(color, (tuple, list)):
        r, g, b = (int(c) for c in color[:3])
    else:
        r, g, b = color.red(), color.green(), color.blue()
    return (b << 16) | (g << 8) | r


def _hwnd(widget: QWidget) -> int | None:
    try:
        return int(widget.winId())
    except Exception:  # noqa: BLE001 - widget may be deleted or not native yet
        return None


def _dwm_set_int(hwnd: int, attribute: int, value: int) -> bool:
    import ctypes
    from ctypes import wintypes

    data = wintypes.DWORD(value)
    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), wintypes.DWORD(attribute), ctypes.byref(data), ctypes.sizeof(data)
    )
    return result == 0


def windows_build() -> int | None:
    """Windows build number (e.g. 22631) or None on other OS / failure."""
    if not _IS_WINDOWS:
        return None
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None


def is_windows_11() -> bool:
    """True on Windows 11 (build >= 22000)."""
    build = windows_build()
    return build is not None and build >= 22000


def set_app_user_model_id(app_id: str = APP_ID) -> bool:
    """Set the explicit AppUserModelID so the taskbar groups/pins the app correctly."""
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes

        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id)) == 0
    except Exception:  # noqa: BLE001
        log.debug("SetCurrentProcessExplicitAppUserModelID failed", exc_info=True)
        return False


def set_caption_colors(widget: QWidget, caption: Any, text: Any, border: Any) -> bool:
    """Tint the Windows 11 title bar (caption, caption text and window border colors)."""
    if not _IS_WINDOWS:
        return False
    try:
        hwnd = _hwnd(widget)
        if hwnd is None:
            return False
        ok = _dwm_set_int(hwnd, DWMWA_CAPTION_COLOR, colorref(caption))
        ok = _dwm_set_int(hwnd, DWMWA_TEXT_COLOR, colorref(text)) and ok
        ok = _dwm_set_int(hwnd, DWMWA_BORDER_COLOR, colorref(border)) and ok
        return ok
    except Exception:  # noqa: BLE001
        log.debug("set_caption_colors failed", exc_info=True)
        return False


def set_dark_title_bar(widget: QWidget, dark: bool) -> bool:
    """Ask DWM for a dark (or light) title bar (Windows 10 1809+)."""
    if not _IS_WINDOWS:
        return False
    try:
        hwnd = _hwnd(widget)
        return hwnd is not None and _dwm_set_int(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, int(bool(dark)))
    except Exception:  # noqa: BLE001
        log.debug("set_dark_title_bar failed", exc_info=True)
        return False


def set_round_corners(widget: QWidget, small: bool = False) -> bool:
    """Request Windows 11 rounded corners (DWMWA_WINDOW_CORNER_PREFERENCE); False elsewhere."""
    if not _IS_WINDOWS:
        return False
    try:
        hwnd = _hwnd(widget)
        pref = DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND
        return hwnd is not None and _dwm_set_int(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, pref)
    except Exception:  # noqa: BLE001
        log.debug("set_round_corners failed", exc_info=True)
        return False


def _set_window_pos(hwnd: int, insert_after: int, flags: int) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    return bool(user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags))


def reassert_topmost(widget: QWidget) -> bool:
    """Re-apply HWND_TOPMOST without activating (keeps floating panels above other topmost windows)."""
    if not _IS_WINDOWS:
        return False
    try:
        hwnd = _hwnd(widget)
        if hwnd is None:
            return False
        return _set_window_pos(hwnd, HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:  # noqa: BLE001
        log.debug("reassert_topmost failed", exc_info=True)
        return False


def _alert(widget: QWidget) -> None:
    try:
        from PySide6.QtWidgets import QApplication

        QApplication.alert(widget, _ALERT_MS)
    except Exception:  # noqa: BLE001
        log.debug("QApplication.alert failed", exc_info=True)


def flash(widget: QWidget) -> bool:
    """Flash the taskbar button until the window comes to the foreground."""
    if _IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]

            hwnd = _hwnd(widget)
            if hwnd is not None:
                info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_TRAY | FLASHW_TIMERNOFG, 3, 0)
                ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
                return True
        except Exception:  # noqa: BLE001
            log.debug("FlashWindowEx failed", exc_info=True)
    _alert(widget)
    return True


def bring_to_front(widget: QWidget, steal_focus: bool = False) -> None:
    """Show *widget* above other windows without stealing keyboard focus (unless *steal_focus*).

    Windows: showNormal if minimized, show, SetWindowPos TOPMOST (no activate) then NOTOPMOST
    after 1.5 s, plus a taskbar alert. Elsewhere: show/raise_/alert. Never touches
    WindowStaysOnTopHint.
    """
    try:
        if widget.isMinimized():
            widget.showNormal()
        else:
            widget.show()
    except Exception:  # noqa: BLE001
        log.debug("bring_to_front: show failed", exc_info=True)
        return

    if _IS_WINDOWS:
        try:
            hwnd = _hwnd(widget)
            if hwnd is not None:
                flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
                _set_window_pos(hwnd, HWND_TOPMOST, flags)

                def _release() -> None:
                    try:
                        _set_window_pos(hwnd, HWND_NOTOPMOST, flags)
                    except Exception:  # noqa: BLE001
                        log.debug("bring_to_front: NOTOPMOST failed", exc_info=True)

                from PySide6.QtCore import QTimer

                QTimer.singleShot(_TOPMOST_RELEASE_MS, _release)
        except Exception:  # noqa: BLE001
            log.debug("bring_to_front: SetWindowPos failed", exc_info=True)
    else:
        try:
            widget.raise_()
        except Exception:  # noqa: BLE001
            log.debug("bring_to_front: raise_ failed", exc_info=True)

    _alert(widget)
    if steal_focus:
        try:
            widget.raise_()
            widget.activateWindow()
        except Exception:  # noqa: BLE001
            log.debug("bring_to_front: activateWindow failed", exc_info=True)


def allow_set_foreground(pid: int) -> bool:
    """Let process *pid* (the running primary instance) take the foreground (AllowSetForegroundWindow)."""
    if not _IS_WINDOWS:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.AllowSetForegroundWindow(int(pid)))
    except Exception:  # noqa: BLE001
        log.debug("AllowSetForegroundWindow failed", exc_info=True)
        return False


def client_animations_enabled() -> bool:
    """SPI_GETCLIENTAREAANIMATION on Windows; True elsewhere or on failure."""
    if not _IS_WINDOWS:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        value = wintypes.BOOL(True)
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
        return bool(value.value) if ok else True
    except Exception:  # noqa: BLE001
        log.debug("SystemParametersInfoW failed", exc_info=True)
        return True
