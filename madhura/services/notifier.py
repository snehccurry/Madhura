"""Native notifications through the system tray balloon (spec §1.12)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

__all__ = ["Notifier"]

log = logging.getLogger(__name__)

_HISTORY_LIMIT = 50


def _resolve_tray(obj: Any) -> Any:
    """Accept a QSystemTrayIcon or a wrapper exposing one (``tray``/``icon``/``tray_icon``)."""
    if obj is None or hasattr(obj, "showMessage"):
        return obj
    for name in ("tray_icon", "tray", "icon", "_tray", "_icon"):
        inner = getattr(obj, name, None)
        if inner is not None and hasattr(inner, "showMessage"):
            return inner
    return None


class Notifier:
    """``notify(title, body)`` → tray.showMessage when a visible tray exists, else just logs.

    *tray_getter* returns the current tray (or None); it is called on every notification so the
    tray can be created after the notifier. ``sent`` keeps the last 50 (title, body) pairs.
    """

    def __init__(self, tray_getter: Callable[[], Any] | None = None) -> None:
        self._tray_getter = tray_getter
        self.sent: list[tuple[str, str]] = []

    def set_tray_getter(self, tray_getter: Callable[[], Any] | None) -> None:
        """Replace the tray getter."""
        self._tray_getter = tray_getter

    def _tray(self) -> Any:
        if self._tray_getter is None:
            return None
        try:
            return _resolve_tray(self._tray_getter())
        except Exception:  # noqa: BLE001 - a broken getter must not break notifications
            log.debug("tray_getter failed", exc_info=True)
            return None

    def can_notify(self) -> bool:
        """True when a visible tray icon can show messages."""
        tray = self._tray()
        if tray is None:
            return False
        try:
            from PySide6.QtWidgets import QSystemTrayIcon

            return bool(tray.isVisible()) and QSystemTrayIcon.supportsMessages()
        except Exception:  # noqa: BLE001
            return False

    def notify(self, title: str, body: str, msecs: int = 6000) -> bool:
        """Show a tray balloon/toast; returns True when shown (otherwise the message is logged)."""
        self.sent.append((title, body))
        del self.sent[:-_HISTORY_LIMIT]
        if self.can_notify():
            try:
                from PySide6.QtWidgets import QSystemTrayIcon

                self._tray().showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, int(msecs))
                return True
            except Exception:  # noqa: BLE001
                log.debug("showMessage failed", exc_info=True)
        log.info("Notification (no tray): %s — %s", title, body)
        return False
