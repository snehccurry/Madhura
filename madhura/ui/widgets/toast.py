"""ToastHost: one-at-a-time pill notifications at the bottom centre of a window.

``ToastHost`` is a QObject (so ``show(text, …)`` does not shadow ``QWidget.show``); it owns a
painted child widget of the window.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QAbstractAnimation, QElapsedTimer, QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPaintEvent, QShortcut
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from madhura.ui import animations, fonts, icons, theme
from madhura.ui.widgets._common import HoverFader, connect_theme, fill_rounded
from madhura.ui.widgets.popup import paint_shadow

try:
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover
    def _is_valid(_obj: object) -> bool:
        return True


__all__ = ["ToastHost", "TOAST_KINDS", "UNDO_TEXT"]

TOAST_KINDS = ("info", "success", "warning", "error")
_HEIGHT = 36
_MARGIN = 14  # shadow margin
_BOTTOM = 24
_MAX_TEXT = 420

#: An action with this label is an undo: besides the click it runs on Ctrl+Z (⌘Z) while it shows.
UNDO_TEXT = "Undo"

_KIND_ICONS = {"success": "check_circle", "warning": "alert", "error": "alert", "danger": "alert"}


def _toast_colors() -> tuple[QColor, QColor, QColor, QColor]:
    """(background, border, text, action) — inverted in the light theme."""
    p = theme.palette()
    if p.is_dark:
        return QColor("#262A33"), QColor(p.border_strong), QColor(p.text), QColor(p.accent)
    accent_dark = theme.ACCENTS.get(p.accent_name, theme.ACCENTS["indigo"])[0]
    return QColor("#1B1D23"), QColor("#1B1D23"), QColor("#F5F6F8"), QColor(accent_dark)


class _ActionLink(QAbstractButton):
    """Compact action text inside the toast ("Undo"). A button to assistive tech (button role and
    a "press" action) that never takes keyboard focus — from the keyboard, an undo runs on Ctrl+Z
    (see :class:`ToastHost`)."""

    def __init__(self, parent: QWidget, on_click: Callable[[], None]) -> None:
        super().__init__(parent)
        self._text = ""  # not QAbstractButton.setText: an "&" in it would grab an Alt mnemonic
        self._hover = HoverFader(self, 100)
        self.setFont(fonts.font(13, 600))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(28)
        self.clicked.connect(lambda _checked=False: on_click())

    def set_text(self, text: str, shortcut: str = "") -> None:
        """Label, plus the keyboard *shortcut* that runs it ("Ctrl+Z") for tooltip and screen readers."""
        self._text = text
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"Shortcut: {shortcut}" if shortcut else "")
        self.setToolTip(f"{text} ({shortcut})" if shortcut else "")
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance(text) + 20)
        self.update()

    def text(self) -> str:  # type: ignore[override]
        return self._text

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        _bg, _border, _fg, action = _toast_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover.value > 0:
            fill_rounded(p, QRectF(self.rect()), 14, theme.with_alpha(action, 0.16 * self._hover.value))
        p.setPen(action)
        p.setFont(self.font())
        p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self._text)
        p.end()


class _ToastWidget(QWidget):
    """The painted pill."""

    hovered = Signal(bool)

    def __init__(self, parent: QWidget, on_action: Callable[[], None]) -> None:
        super().__init__(parent)
        self.setObjectName("mhToast")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.kind = "info"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(_MARGIN + 16, _MARGIN, _MARGIN + 4, _MARGIN)
        lay.setSpacing(6)
        self.label = QLabel(self)
        self.label.setFont(fonts.font(13, 500))
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.label, 1)
        self.action = _ActionLink(self, on_action)
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(_HEIGHT + 2 * _MARGIN)
        self.hide()
        # Re-colour live: the app-wide QSS re-polish that follows a theme switch resets the
        # label's palette, so a toast shown across the switch would keep the old text colour.
        connect_theme(self, self._on_theme_changed)

    def _on_theme_changed(self) -> None:
        self._sync_palette()
        self.update()

    def configure(self, text: str, kind: str, action_text: str | None, shortcut: str = "") -> None:
        self.kind = kind if kind in _KIND_ICONS or kind == "info" else "info"
        fm = QFontMetrics(self.label.font())
        shown = fm.elidedText(text, Qt.TextElideMode.ElideRight, _MAX_TEXT)
        self.label.setText(shown)
        self.label.setToolTip(text if shown != text else "")
        self.setAccessibleName(text)
        has_icon = self.kind in _KIND_ICONS
        left = _MARGIN + (40 if has_icon else 18)
        right = _MARGIN + (4 if action_text else 18)
        self.layout().setContentsMargins(left, _MARGIN, right, _MARGIN)
        if action_text:
            self.action.set_text(action_text, shortcut)
            self.action.show()
        else:
            self.action.hide()
        width = left + fm.horizontalAdvance(shown) + right + (self.action.width() + 6 if action_text else 0)
        self.setFixedWidth(width)
        self._sync_palette()

    def _sync_palette(self) -> None:
        _bg, _border, fg, _action = _toast_colors()
        pal = self.label.palette()
        pal.setColor(self.label.foregroundRole(), fg)
        self.label.setPalette(pal)

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self.hovered.emit(True)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self.hovered.emit(False)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self._sync_palette()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        bg, border, _fg, _action = _toast_colors()
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_shadow(p, QRectF(self.rect()), _HEIGHT / 2.0, _MARGIN, blur=20, offset_y=6, alpha=0.5 if pal.is_dark else 0.22)
        card = QRectF(self.rect()).adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        fill_rounded(p, card, _HEIGHT / 2.0, bg, border)
        if self.kind in _KIND_ICONS:
            col = {"success": pal.success, "warning": pal.warning}.get(self.kind, pal.danger)
            if not pal.is_dark:
                col = {"success": "#3CCB8F", "warning": "#F2B84B"}.get(self.kind, "#F0616D")
            pm = icons.pixmap(_KIND_ICONS[self.kind], 16, col, self.devicePixelRatioF())
            p.drawPixmap(int(card.left() + 16), int(card.top() + (_HEIGHT - 16) / 2), pm)
        p.end()


class ToastHost(QObject):
    """Shows one toast at a time, bottom-centre, 24 px above ``parent_window``'s bottom edge —
    centred on ``center_widget`` when given (the main window's page area, so a toast lines up
    with the page column rather than with page + sidebar).

    ``show(...)`` returns a token. A new toast replaces the current one (which then *expires*).
    Hovering pauses the countdown. A toast shown while the window is hidden or minimized (e.g.
    the "finished while Madhura was closed" notice at an autostart into the tray) waits: its
    countdown starts when the window is next shown. Signals: ``expired(int token)`` (timeout,
    replacement, :meth:`dismiss` or :meth:`flush`) and ``actioned(int token)`` (action clicked;
    the callback runs first). Exactly one of the two fires per token.

    An action labelled :data:`UNDO_TEXT` also runs on the platform's Undo key (Ctrl+Z / ⌘Z) while
    its toast shows — the keyboard route to it (the toast never takes focus). A focused text field
    keeps the key for its own undo: it claims it through ShortcutOverride, so the toast's shortcut
    doesn't fire there.
    """

    expired = Signal(int)
    actioned = Signal(int)

    def __init__(self, parent_window: QWidget, center_widget: QWidget | None = None) -> None:
        super().__init__(parent_window)
        self._window = parent_window
        self._center = center_widget if center_widget is not parent_window else None
        self._widget = _ToastWidget(parent_window, self._on_action)
        self._widget.hovered.connect(self._on_hover)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire_current)
        self._clock = QElapsedTimer()
        self._remaining = 0
        self._held = False  # shown while the window was hidden: countdown starts on show
        self._token = 0
        self._current: int | None = None
        self._callback: Callable[[], object] | None = None
        self._anim: QPropertyAnimation | None = None
        self._undo_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), parent_window)
        self._undo_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._undo_shortcut.setEnabled(False)  # only while an "Undo" toast shows
        self._undo_shortcut.activated.connect(self.trigger_action)
        parent_window.installEventFilter(self)
        if self._center is not None:
            self._center.installEventFilter(self)  # e.g. the sidebar collapsing to its rail
        top = parent_window.window()
        if top is not parent_window:
            top.installEventFilter(self)  # Show / un-minimize arrive at the top-level window

    # ------------------------------------------------------------------ API
    def show(
        self,
        text: str,
        kind: str = "info",
        action_text: str | None = None,
        on_action: Callable[[], object] | None = None,
        duration_ms: int = 4000,
    ) -> int:
        """Show a toast and return its token. ``kind``: info | success | warning | error."""
        if self._current is not None:
            self._end(expired=True, animate=False)
        self._token += 1
        token = self._token
        self._current = token
        self._callback = on_action
        undo = bool(action_text == UNDO_TEXT and on_action is not None)
        w = self._widget
        w.configure(text, kind, action_text, self.undo_key_text() if undo else "")
        self._undo_shortcut.setEnabled(undo)
        self._place(offset=8 if animations.motion_enabled() else 0)
        w.show()
        w.raise_()
        self._animate_to(self._rest_pos(), fade_in=True)
        self._remaining = max(1000, int(duration_ms))
        self._held = not self.window_on_screen()
        if not self._held:
            self._start_countdown()
        return token

    def window_on_screen(self) -> bool:
        """True while the host window is shown and not minimized."""
        top = self._window.window()
        return bool(self._window.isVisible() and not top.isMinimized())

    def is_held(self) -> bool:
        """True while the current toast waits for the window to be shown."""
        return self._current is not None and self._held

    def current_token(self) -> int | None:
        """Token of the visible toast (None if none)."""
        return self._current

    def is_showing(self) -> bool:
        """True while a toast is visible."""
        return self._current is not None

    def text(self) -> str:
        """Text of the visible toast."""
        return self._widget.label.text() if self._current is not None else ""

    def action_text(self) -> str:
        """Action label of the visible toast ("" if none)."""
        return self._widget.action.text() if self._current is not None and self._widget.action.isVisible() else ""

    def widget(self) -> QWidget:
        """The toast widget (for screenshots/tests)."""
        return self._widget

    def trigger_action(self) -> None:
        """Programmatically click the action (tests / keyboard shortcut)."""
        if self._current is not None and self._widget.action.isVisible():
            self._on_action()

    def undo_shortcut(self) -> QShortcut:
        """The Undo-key shortcut (enabled while an "Undo" toast shows)."""
        return self._undo_shortcut

    @staticmethod
    def undo_key_text() -> str:
        """The Undo key as the platform writes it ("Ctrl+Z", "⌘Z")."""
        return QKeySequence(QKeySequence.StandardKey.Undo).toString(QKeySequence.SequenceFormat.NativeText)

    def dismiss(self) -> None:
        """Expire the current toast now (animated)."""
        if self._current is not None:
            self._end(expired=True, animate=True)

    def flush(self) -> None:
        """Expire the current toast synchronously (e.g. on quit, to commit undo-deletes)."""
        if self._current is not None:
            self._end(expired=True, animate=False)

    # ------------------------------------------------------------------ internals
    def _rest_pos(self) -> QPoint:
        w = self._widget
        win_w = self._window.width()
        center = self._center
        if center is not None and _is_valid(center) and center.isVisible():
            left = center.mapTo(self._window, QPoint(0, 0)).x()
            x = left + (center.width() - w.width()) // 2
            x = max(8 - _MARGIN, min(x, win_w - w.width() + _MARGIN - 8))
        else:
            x = (win_w - w.width()) // 2
        y = self._window.height() - _BOTTOM - _HEIGHT - _MARGIN
        return QPoint(x, y)

    def _place(self, offset: int = 0) -> None:
        self._widget.move(self._rest_pos() + QPoint(0, offset))

    def _stop_anim(self) -> None:
        anim = self._anim
        self._anim = None
        if anim is not None and _is_valid(anim):  # deletes itself once it has finished
            anim.stop()

    def _animate_to(self, pos: QPoint, fade_in: bool) -> None:
        self._stop_anim()
        if not animations.motion_enabled():
            self._widget.move(pos)
            return
        anim = QPropertyAnimation(self._widget, b"pos", self)
        anim.setDuration(200)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setEndValue(pos)
        self._anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        if fade_in:
            animations.fade_in(self._widget, 180)

    def _end(self, expired: bool, animate: bool) -> None:
        token = self._current
        if token is None:
            return
        self._current = None
        self._callback = None
        self._held = False
        self._undo_shortcut.setEnabled(False)
        self._timer.stop()
        if animate and animations.motion_enabled() and self._widget.isVisible():
            animations.fade_out(self._widget, 140)
        else:
            self._stop_anim()
            animations.stop_animations(self._widget)
            self._widget.setGraphicsEffect(None)
            self._widget.hide()
        if expired:
            self.expired.emit(token)

    def _expire_current(self) -> None:
        self._end(expired=True, animate=True)

    def _start_countdown(self) -> None:
        self._clock.start()
        self._timer.start(self._remaining)

    def _release_held(self) -> None:
        if self._held and self._current is not None and self.window_on_screen():
            self._held = False
            self._place()
            self._start_countdown()

    def _on_action(self) -> None:
        token = self._current
        if token is None:
            return
        cb = self._callback
        self._end(expired=False, animate=True)
        if cb is not None:
            cb()
        self.actioned.emit(token)

    def _on_hover(self, inside: bool) -> None:
        if self._current is None:
            return
        if inside:
            if self._timer.isActive():
                self._remaining = max(0, self._remaining - int(self._clock.elapsed()))
                self._timer.stop()
        else:
            if not self._timer.isActive() and not self._held:
                self._remaining = max(1500, self._remaining)
                self._clock.restart()
                self._timer.start(self._remaining)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        moved = (obj is self._window and kind == QEvent.Type.Resize) or (
            self._center is not None and obj is self._center and kind in (QEvent.Type.Resize, QEvent.Type.Move)
        )
        if moved and _is_valid(self._widget) and self._widget.isVisible():
            self._place()
        if self._held and kind in (QEvent.Type.Show, QEvent.Type.WindowStateChange):
            # Shown / restored: start the countdown once the state change has settled.
            QTimer.singleShot(0, self, self._release_held)
        return False
