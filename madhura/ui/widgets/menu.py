"""Menu: a styled QMenu with muted right-aligned shortcut text and accent check marks.

On Windows 11 the popup is rounded by DWM (``DWMWA_WINDOW_CORNER_PREFERENCE``); elsewhere it is a
frameless translucent window whose rounded corners and border come from the QSS.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAction, QColor, QFontMetrics, QGuiApplication, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QMenu, QWidget

from madhura.ui import fonts, icons, theme

__all__ = ["Menu", "is_windows_11", "round_window_corners"]

_SHORTCUT_PROP = "mh_shortcut"
_DANGER_PROP = "mh_danger"


def is_windows_11() -> bool:
    """True on Windows 11 (build ≥ 22000)."""
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22000  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def round_window_corners(widget: QWidget, small: bool = False) -> bool:
    """Ask DWM (Windows 11) to round ``widget``'s window corners. Returns True on success."""
    if not is_windows_11():
        return False
    try:
        import ctypes

        dwmwa_window_corner_preference = 33
        pref = ctypes.c_int(3 if small else 2)  # DWMWCP_ROUNDSMALL / DWMWCP_ROUND
        hwnd = ctypes.c_void_p(int(widget.winId()))
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, ctypes.c_uint(dwmwa_window_corner_preference), ctypes.byref(pref), ctypes.sizeof(pref)
        )
        return res == 0
    except (OSError, AttributeError, ValueError, TypeError):
        return False


class Menu(QMenu):
    """Styled popup menu. Build it with :meth:`add_action` / :meth:`add_separator` /
    :meth:`add_menu`; show it with :meth:`popup_below` or ``popup(pos)``."""

    def __init__(self, parent: QWidget | None = None, title: str = "") -> None:
        super().__init__(title, parent)
        self._native_round = is_windows_11()
        self._min_width = 0
        if self._native_round:
            self.setProperty("nativeRound", "true")
        else:
            self.setWindowFlags(
                self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFont(fonts.font(13))
        self.setSeparatorsCollapsible(True)
        self.setToolTipsVisible(True)
        self.aboutToShow.connect(self._prepare)

    # ------------------------------------------------------------------ building
    def add_action(
        self,
        text: str,
        callback: Callable[[], Any] | None = None,
        icon: str | None = None,
        shortcut_text: str | None = None,
        checkable: bool = False,
        checked: bool = False,
        enabled: bool = True,
        *,
        danger: bool = False,
        data: Any = None,
        tooltip: str | None = None,
    ) -> QAction:
        """Append an action and return it. ``callback`` is called with no arguments.

        ``shortcut_text`` is display-only (e.g. "Ctrl+H"); it does not register a shortcut.
        ``danger`` tints the icon with the danger colour.
        """
        act = QAction(text, self)
        if icon:
            color = theme.palette().danger if danger else None
            act.setIcon(icons.icon(icon, color))
        if checkable:
            act.setCheckable(True)
            act.setChecked(checked)
        act.setEnabled(enabled)
        if shortcut_text:
            act.setProperty(_SHORTCUT_PROP, shortcut_text)
        if danger:
            act.setProperty(_DANGER_PROP, True)
        if data is not None:
            act.setData(data)
        if tooltip:
            act.setToolTip(tooltip)
        if callback is not None:
            act.triggered.connect(lambda _checked=False, cb=callback: cb())
        self.addAction(act)
        self._prepare()
        return act

    def add_separator(self) -> QAction:
        """Append a separator."""
        return self.addSeparator()

    def add_menu(self, title: str, icon: str | None = None) -> "Menu":
        """Append and return a styled submenu."""
        sub = Menu(self, title)
        if icon:
            sub.setIcon(icons.icon(icon))
        self.addMenu(sub)
        return sub

    def set_min_width(self, px: int) -> None:
        """Minimum popup width (e.g. to match a Select button)."""
        self._min_width = int(px)
        self._prepare()

    # ------------------------------------------------------------------ showing
    def popup_below(self, widget: QWidget, align: str = "left", gap: int = 4) -> None:
        """Pop up under ``widget`` (``align`` "left" or "right"), flipping above if needed."""
        self.ensurePolished()
        self._prepare()
        size = self.sizeHint()
        top_left = widget.mapToGlobal(QPoint(0, widget.height() + gap))
        if align == "right":
            top_left.setX(widget.mapToGlobal(QPoint(widget.width(), 0)).x() - size.width())
        screen = QGuiApplication.screenAt(top_left) or widget.screen()
        if screen is not None:
            avail: QRect = screen.availableGeometry()
            if top_left.y() + size.height() > avail.bottom():
                top_left.setY(widget.mapToGlobal(QPoint(0, 0)).y() - gap - size.height())
            top_left.setX(max(avail.left() + 4, min(top_left.x(), avail.right() - size.width() - 4)))
        self.popup(top_left)

    # ------------------------------------------------------------------ sizing + painting
    def _shortcut_width(self) -> int:
        fm = QFontMetrics(self.font())
        widths = [
            fm.horizontalAdvance(str(a.property(_SHORTCUT_PROP)))
            for a in self.actions()
            if a.isVisible() and a.property(_SHORTCUT_PROP)
        ]
        return (max(widths) + 28) if widths else 0

    def _prepare(self) -> None:
        """Reserve room for shortcut texts by raising the minimum width (QMenu sizes its action
        rects from minimumWidth, not from the window width)."""
        self.setMinimumWidth(0)
        base = QMenu.sizeHint(self).width()
        self.setMinimumWidth(max(base + self._shortcut_width(), self._min_width, 180))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._native_round:
            round_window_corners(self)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(self.font())
        dpr = self.devicePixelRatioF()
        for act in self.actions():
            if not act.isVisible() or act.isSeparator():
                continue
            r = self.actionGeometry(act)
            if r.isNull():
                continue
            enabled = act.isEnabled()
            sc = act.property(_SHORTCUT_PROP)
            if sc:
                p.setPen(QColor(pal.text_subtle if not enabled else pal.text_muted))
                p.drawText(
                    r.adjusted(0, 0, -12, 0),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(sc),
                )
            if act.isCheckable() and act.isChecked():
                col = pal.accent if enabled else pal.text_subtle
                pm = icons.pixmap("check", 16, col, dpr)
                p.drawPixmap(r.left() + 8, r.top() + (r.height() - 16) // 2, pm)
        p.end()
