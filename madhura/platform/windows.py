"""Windows integration via ctypes (spec §7.7). Every function is safe to call on any OS.

On non-Windows platforms the functions fall back to portable Qt behaviour or do nothing.
No function in this module ever raises.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from madhura import APP_ID, APP_NAME

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

__all__ = [
    "set_app_user_model_id",
    "register_app_user_model_id",
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

# ShowWindow
SW_SHOWNOACTIVATE = 4

#: QWindow property Qt's platform plugins read on show: Windows uses SW_SHOWNOACTIVATE, xcb skips
#: the focus-stealing user time, Cocoa orders front without making the window key.
_NO_ACTIVATE_PROP = "_q_showWithoutActivating"

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


def register_app_user_model_id(
    app_id: str = APP_ID, display_name: str = APP_NAME, icon_path: str | os.PathLike[str] | None = None
) -> bool:
    """Give the explicit AUMID a name (and icon) for notifications and Settings › Notifications.

    Without a Start-menu shortcut carrying the AUMID (the portable build has none), Windows looks
    it up under ``HKCU\\Software\\Classes\\AppUserModelId\\<id>``; unregistered, toasts show the raw
    ID. Writes only values that differ. Best effort: returns False on other OS or failure.
    """
    if not _IS_WINDOWS:
        return False
    try:
        import winreg

        values = {"DisplayName": str(display_name)}
        if icon_path is not None and os.path.isfile(icon_path):
            values["IconUri"] = os.path.abspath(icon_path)
        key_path = f"Software\\Classes\\AppUserModelId\\{app_id}"
        access = winreg.KEY_READ | winreg.KEY_WRITE
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, access) as key:
            for name, value in values.items():
                try:
                    current = winreg.QueryValueEx(key, name)
                except OSError:
                    current = None
                if current is None or current[0] != value:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        return True
    except Exception:  # noqa: BLE001 - cosmetic; never block startup
        log.warning("Could not register the AppUserModelID %s", app_id, exc_info=True)
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


def _show_no_activate_native(widget: QWidget) -> bool:
    """Windows: restore a minimized window with ShowWindow(SW_SHOWNOACTIVATE) (Qt syncs its state
    from the resulting WM_SIZE). Only for windows that were not maximized: SW_SHOWNOACTIVATE
    restores to the normal rect."""
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = _hwnd(widget)
        if hwnd is None:
            return False
        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        return True
    except Exception:  # noqa: BLE001
        log.debug("ShowWindow(SW_SHOWNOACTIVATE) failed", exc_info=True)
        return False


def _show_without_activating(widget: QWidget) -> None:
    """``widget.show()`` with Qt's no-activate window property set just for this call.

    Not via WA_ShowWithoutActivating: Qt copies that to the property and never clears it, so a
    later show_main(activate=True) would come up inactive too.
    """
    window = widget.windowHandle()
    if window is None:
        widget.winId()  # create the native window so the property can be set before showing
        window = widget.windowHandle()
    if window is None:
        widget.show()
        return
    previous = window.property(_NO_ACTIVATE_PROP)
    window.setProperty(_NO_ACTIVATE_PROP, True)
    try:
        widget.show()
    finally:
        window.setProperty(_NO_ACTIVATE_PROP, previous)


def _restore_and_show(widget: QWidget, steal_focus: bool) -> None:
    """Un-minimize (keeping a maximized window maximized) and show *widget*; unless *steal_focus*,
    without activating it where the platform allows."""
    from PySide6.QtCore import Qt

    state = widget.windowState()
    maximized = bool(state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen))
    if widget.isMinimized():
        native = _IS_WINDOWS and not steal_focus and not maximized and widget.isVisible()
        if not (native and _show_no_activate_native(widget)):
            # Clear only the minimized bit: showNormal() would also drop Maximized.
            widget.setWindowState(state & ~Qt.WindowState.WindowMinimized)
    if widget.isVisible():
        return
    if steal_focus or (_IS_WINDOWS and maximized):
        # Windows maps "show without activating" to SW_SHOWNOACTIVATE, which would also restore
        # a maximized window to its normal size.
        widget.show()
    else:
        _show_without_activating(widget)


def _release_topmost(widget: QWidget) -> None:
    """Drop HWND_TOPMOST again (z-order only: SWP_SHOWWINDOW here would re-show a window the user
    hid meanwhile behind Qt's back). Also runs for a hidden window, which would otherwise stay
    always-on-top the next time it is shown."""
    try:
        hwnd = _hwnd(widget)
        if hwnd is not None:
            _set_window_pos(hwnd, HWND_NOTOPMOST, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:  # noqa: BLE001
        log.debug("bring_to_front: NOTOPMOST failed", exc_info=True)


def bring_to_front(widget: QWidget, steal_focus: bool = False) -> None:
    """Show *widget* above other windows without stealing keyboard focus (unless *steal_focus*).

    Un-minimizes (a maximized window stays maximized) and shows without activation. Windows:
    then SetWindowPos TOPMOST (no activate) and NOTOPMOST after 1.5 s, plus a taskbar alert.
    Elsewhere: show/raise_/alert. Never touches WindowStaysOnTopHint.
    """
    try:
        _restore_and_show(widget, steal_focus)
    except Exception:  # noqa: BLE001
        log.debug("bring_to_front: show failed", exc_info=True)
        return

    if _IS_WINDOWS:
        try:
            hwnd = _hwnd(widget)
            if hwnd is not None:
                flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
                _set_window_pos(hwnd, HWND_TOPMOST, flags)

                from PySide6.QtCore import QTimer

                # widget as context: no callback into a deleted window.
                QTimer.singleShot(_TOPMOST_RELEASE_MS, widget, lambda: _release_topmost(widget))
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
