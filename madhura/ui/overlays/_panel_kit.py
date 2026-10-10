"""Private building blocks shared by the [O] floating panels (check-in, nudge, away).

Everything paints from ``theme.current().palette`` at paint time, so the panels follow theme and
accent switches without being rebuilt.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPaintEvent,
    QTextLayout,
    QWindow,
)
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

try:  # shiboken6 ships with PySide6
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover
    def _is_valid(_obj: object) -> bool:
        return True

from madhura.ui import animations, fonts, icons, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import SIZES, Button, _variant_colors
from madhura.ui.widgets.labels import Micro

__all__ = [
    "quote",
    "clamp_lines",
    "Glyph",
    "ClampText",
    "CountdownBar",
    "HoverCountdown",
    "MenuButton",
    "FocusGuard",
    "install_focus_guard",
    "panel_header",
    "vbox",
    "hbox",
]

#: Tone → (soft background token, foreground token) for :class:`Glyph`.
_TONES: dict[str, tuple[str, str]] = {
    "accent": ("accent_soft", "accent"),
    "success": ("success_soft", "success"),
    "warning": ("warning_soft", "warning"),
    "break": ("break_soft", "break_"),
    "neutral": ("hover", "text_muted"),
}


def quote(text: str, limit: int = 40) -> str:
    """``‘text’`` with the text cut to *limit* characters (ellipsis) — matches the Focus page."""
    text = (text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"‘{text}’"


def clamp_lines(text: str, font: QFont, width: int, max_lines: int) -> list[str]:
    """Word-wrap *text* to *width* px and keep at most *max_lines* lines (the last one elided)."""
    width = max(1, int(width))
    fm = QFontMetrics(font)
    out: list[str] = []
    layout = QTextLayout(text, font)
    layout.beginLayout()
    spans: list[tuple[int, int]] = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        spans.append((line.textStart(), line.textLength()))
    layout.endLayout()
    for i, (start, length) in enumerate(spans):
        if i == max_lines - 1 and len(spans) > max_lines:
            rest = text[start:].replace("\n", " ").strip()
            out.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
            break
        out.append(text[start : start + length].rstrip())
    return out or [""]


def vbox(parent: QWidget | None = None, spacing: int = 0) -> QVBoxLayout:
    """Zero-margin QVBoxLayout."""
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


def hbox(parent: QWidget | None = None, spacing: int = 0) -> QHBoxLayout:
    """Zero-margin QHBoxLayout."""
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


class Glyph(QWidget):
    """Round soft-tinted medallion with a centred icon (``tone``: accent/success/warning/break/neutral)."""

    def __init__(self, icon: str, diameter: int = 32, parent: QWidget | None = None, *, tone: str = "accent") -> None:
        super().__init__(parent)
        if not icons.has(icon):
            raise KeyError(icon)
        self._icon = icon
        self._tone = tone if tone in _TONES else "accent"
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def icon_name(self) -> str:
        """Current icon."""
        return self._icon

    def tone(self) -> str:
        """Current tone."""
        return self._tone

    def set_icon(self, name: str, tone: str | None = None) -> None:
        """Change the icon (and optionally the tone)."""
        if not icons.has(name):
            raise KeyError(name)
        self._icon = name
        if tone is not None and tone in _TONES:
            self._tone = tone
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        bg_token, fg_token = _TONES[self._tone]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(getattr(pal, bg_token)))
        p.drawEllipse(QRectF(self.rect()))
        g = max(12, int(round(self.width() * 0.5)))
        pm = icons.pixmap(self._icon, g, getattr(pal, fg_token), self.devicePixelRatioF())
        p.drawPixmap((self.width() - g) // 2, (self.height() - g) // 2, pm)
        p.end()


class ClampText(QWidget):
    """Left-aligned text wrapped to at most ``max_lines`` lines (last line elided).

    ``color`` is a palette token ("text", "text_muted", "success" …) resolved at paint time.
    """

    def __init__(
        self,
        text: str = "",
        size_px: int = 15,
        weight: int = 600,
        max_lines: int = 2,
        parent: QWidget | None = None,
        *,
        color: str = "text",
        tabular: bool = False,
        line_spacing: int = 2,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._max_lines = max(1, int(max_lines))
        self._color = color
        self._line_spacing = line_spacing
        self.setFont(fonts.font(size_px, weight, tabular=tabular))
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName(text)

    # ------------------------------------------------------------------ API
    def text(self) -> str:
        """Full (unclamped) text."""
        return self._text

    def set_text(self, text: str) -> None:
        """Replace the text."""
        if text == self._text:
            return
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def set_color(self, token: str) -> None:
        """Palette token for the text colour."""
        self._color = token
        self.update()

    def lines(self, width: int | None = None) -> list[str]:
        """The rendered lines at *width* (default: current width)."""
        return clamp_lines(self._text, self.font(), width if width is not None else self.width(), self._max_lines)

    # ------------------------------------------------------------------ sizing
    def _line_h(self) -> int:
        return QFontMetrics(self.font()).height()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        n = len(self.lines(width))
        return n * self._line_h() + (n - 1) * self._line_spacing

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        w = min(fm.horizontalAdvance(self._text), 320)
        return QSize(max(40, w), self._line_h())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, self._line_h())

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self.font())
        p.setPen(QColor(getattr(pal, self._color, pal.text)))
        lh = self._line_h()
        y = 0
        for line in self.lines():
            p.drawText(QRectF(0, y, self.width(), lh), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), line)
            y += lh + self._line_spacing
        p.end()


class CountdownBar(QWidget):
    """Thin (2 px) rounded countdown bar: a ``fraction`` of the width in ``tone`` over the track.

    ``tone`` is a palette token ("accent", "break_" …) or "neutral" (soft grey for countdowns that
    only auto-close something).
    """

    def __init__(self, parent: QWidget | None = None, *, tone: str = "accent", height: int = 2) -> None:
        super().__init__(parent)
        self._fraction = 1.0
        self._tone = tone
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("Time left to answer")

    def fraction(self) -> float:
        """Shown fraction 0..1."""
        return self._fraction

    def set_fraction(self, value: float) -> None:
        """Set the remaining fraction (repaints only when it moves ≥ half a pixel)."""
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._fraction) * max(1, self.width()) < 0.5 and value not in (0.0, 1.0):
            return
        self._fraction = value
        self.setAccessibleDescription(f"{int(round(value * 100))}% of the time left")
        self.update()

    def set_tone(self, tone: str) -> None:
        """Fill colour: a palette token ("accent", "break_" …) or "neutral" (soft grey)."""
        self._tone = tone
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        radius = r.height() / 2.0
        p.setBrush(QColor(pal.border_strong if pal.is_dark else pal.border))
        p.drawRoundedRect(r, radius, radius)
        w = r.width() * self._fraction
        if w > 0.5:
            if self._tone == "neutral":
                fill = QColor(pal.text_subtle)
                fill.setAlphaF(0.75 if pal.is_dark else 0.55)
            else:
                fill = QColor(getattr(pal, self._tone, pal.accent))
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(r.left(), r.top(), max(w, r.height()), r.height()), radius, radius)
        p.end()


class HoverCountdown:
    """Real-time countdown that pauses while hovered and resumes with at least ``min_resume_ms``.

    Drives an optional :class:`CountdownBar` (updated every ``tick_ms``) and calls ``on_expire``
    once. Used for the 6 s reason step, the away panel and the nudge auto-hide.
    """

    def __init__(
        self,
        owner: QWidget,
        duration_ms: int,
        on_expire: Callable[[], None],
        *,
        bar: CountdownBar | None = None,
        min_resume_ms: int = 3000,
        tick_ms: int = 100,
    ) -> None:
        self._duration = max(1, int(duration_ms))
        self._on_expire = on_expire
        self._bar = bar
        self._min_resume = int(min_resume_ms)
        self._left_when_paused: int | None = None
        self._expired = False
        self._timer = QTimer(owner)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire)
        self._tick = QTimer(owner)
        self._tick.setInterval(tick_ms if animations.motion_enabled() else max(tick_ms, 500))
        self._tick.timeout.connect(self._update_bar)

    @property
    def duration_ms(self) -> int:
        """Total duration."""
        return self._duration

    def start(self, duration_ms: int | None = None) -> None:
        """(Re)start the countdown."""
        if duration_ms is not None:
            self._duration = max(1, int(duration_ms))
        self._expired = False
        self._left_when_paused = None
        self._timer.start(self._duration)
        self._tick.start()
        self._update_bar()

    def stop(self) -> None:
        """Stop without expiring."""
        self._timer.stop()
        self._tick.stop()
        self._left_when_paused = None

    def is_running(self) -> bool:
        """True while counting (including while paused by hover)."""
        return self._timer.isActive() or self._left_when_paused is not None

    def is_paused(self) -> bool:
        """True while frozen by hover."""
        return self._left_when_paused is not None

    def remaining_ms(self) -> int:
        """Milliseconds left (frozen while hovered; 0 when stopped/expired)."""
        if self._left_when_paused is not None:
            return self._left_when_paused
        if self._timer.isActive():
            return max(0, self._timer.remainingTime())
        return 0

    def set_hover(self, hovered: bool) -> None:
        """Freeze on hover; resume with at least ``min_resume_ms`` when the pointer leaves."""
        if self._expired:
            return
        if hovered:
            if self._timer.isActive():
                self._left_when_paused = max(0, self._timer.remainingTime())
                self._timer.stop()
                self._update_bar()
        elif self._left_when_paused is not None:
            left = max(self._left_when_paused, self._min_resume)
            self._left_when_paused = None
            self._timer.start(left)
            self._update_bar()

    def expire_now(self) -> None:
        """Expire immediately (tests, keyboard)."""
        self._expire()

    def _update_bar(self) -> None:
        if self._bar is None:
            return
        try:
            self._bar.set_fraction(self.remaining_ms() / float(self._duration))
        except RuntimeError:  # the bar was deleted with its page
            self._bar = None

    def _expire(self) -> None:
        if self._expired:
            return
        self._expired = True
        self._timer.stop()
        self._tick.stop()
        self._left_when_paused = None
        if self._bar is not None:
            try:
                self._bar.set_fraction(0.0)
            except RuntimeError:
                self._bar = None
        self._on_expire()


def panel_header(text: str, parent: QWidget, trailing: QWidget | None = None) -> QHBoxLayout:
    """The micro-caps caption row of a panel ("CHECK-IN" … optional trailing widget)."""
    row = hbox(spacing=8)
    micro = Micro(text, parent)
    micro.setObjectName("panelCaption")
    row.addWidget(micro, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    if trailing is not None:
        row.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class MenuButton(Button):
    """:class:`Button` with a trailing chevron — "Snooze ▾" (opens a menu on click)."""

    _CHEVRON = 14
    _CHEVRON_GAP = 4

    def __init__(self, text: str, variant: str = "secondary", size: str = "md", parent: QWidget | None = None) -> None:
        super().__init__(text, variant, None, size, parent)
        self.setAccessibleDescription("Opens a menu")

    def sizeHint(self) -> QSize:  # noqa: N802
        base = super().sizeHint()
        return QSize(base.width() + self._CHEVRON + self._CHEVRON_GAP - 4, base.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        bg, border, fg = _variant_colors(self.variant(), self._hover.value, self.isDown())
        fill_rounded(p, QRectF(self.rect()), 8.0, bg, border)
        _h, _pad, _fpx, _ipx = SIZES[self.button_size()]
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self.text())
        content = tw + self._CHEVRON_GAP + self._CHEVRON
        x = (self.width() - content) / 2.0
        p.setPen(fg)
        p.setFont(self.font())
        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        p.drawText(QRectF(x, 0, tw + 1, self.height()), align, self.text())
        pm = icons.pixmap("chevron_down", self._CHEVRON, fg, self.devicePixelRatioF())
        p.drawPixmap(int(round(x + tw + self._CHEVRON_GAP)), int(round((self.height() - self._CHEVRON) / 2.0)) + 1, pm)
        p.end()


class FocusGuard(QObject):
    """Hands activation straight back when a never-focus window gets activated anyway.

    Floating panels and the pill are ``WindowDoesNotAcceptFocus`` + ``WA_ShowWithoutActivating``;
    Windows, macOS and X11 honour that, but some platform plugins (Qt's offscreen plugin, a few
    window managers) activate every shown window. If that happens and one of our own normal
    windows was active just before, it is re-activated so typing never lands in a panel. When no
    window of ours was active (the user is in another app) nothing is done — re-activating ours
    would steal focus from that app.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._prev: QWindow | None = None
        self.restored = 0
        self._remember(QGuiApplication.focusWindow())
        app = QGuiApplication.instance()
        if app is not None:
            app.focusWindowChanged.connect(self._on_focus_window)  # type: ignore[attr-defined]

    @staticmethod
    def _accepts_focus(win: QWindow | None) -> bool:
        return win is not None and _is_valid(win) and not (win.flags() & Qt.WindowType.WindowDoesNotAcceptFocus)

    def _remember(self, win: QWindow | None) -> None:
        """Track the last focus window; forget it the moment it is destroyed (no dangling refs)."""
        old = self._prev
        if old is win:
            return
        if old is not None and _is_valid(old):
            try:
                old.destroyed.disconnect(self._forget)
            except (RuntimeError, TypeError):
                pass
        self._prev = win
        if win is not None:
            win.destroyed.connect(self._forget)

    def _forget(self, *_args: object) -> None:
        self._prev = None

    def _on_focus_window(self, win: QWindow | None) -> None:
        if win is None or self._accepts_focus(win):
            self._remember(win)
            return
        prev = self._prev
        if prev is not None and self._accepts_focus(prev) and prev.isVisible():
            self.restored += 1
            prev.requestActivate()


_GUARD: list[FocusGuard] = []


def install_focus_guard() -> FocusGuard | None:
    """Create the app-wide :class:`FocusGuard` once (no-op without a QGuiApplication)."""
    app = QGuiApplication.instance()
    if app is None:
        return None
    if _GUARD and _is_valid(_GUARD[0]) and _GUARD[0].parent() is app:
        return _GUARD[0]
    _GUARD.clear()
    guard = FocusGuard(app)
    _GUARD.append(guard)
    return guard
