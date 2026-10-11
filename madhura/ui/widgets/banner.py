"""InlineBanner: a calm, tinted message strip with inline actions."""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

from madhura.ui import fonts, icons, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import IconButton, LinkButton

__all__ = ["InlineBanner", "BANNER_TONES"]

BANNER_TONES = ("info", "warning", "success", "accent", "danger")

_DEFAULT_ICONS = {
    "info": "info",
    "warning": "alert",
    "success": "check_circle",
    "accent": "sparkle",
    "danger": "alert",
}

Action = tuple[str, Callable[[], object]]


class InlineBanner(QWidget):
    """Tinted banner: leading icon, wrapping text, inline text actions, optional dismiss ×.

    ``actions`` is ``[(label, callback), …]``; the first action is emphasised. Emits
    ``dismissed`` (and hides itself) when × is clicked, and ``actionTriggered(str label)``.
    """

    dismissed = Signal()
    actionTriggered = Signal(str)

    def __init__(
        self,
        tone: str = "info",
        text: str = "",
        actions: Sequence[Action] | None = None,
        dismissible: bool = False,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tone = tone if tone in BANNER_TONES else "info"
        self._icon = icon
        self._action_buttons: list[LinkButton] = []
        self._stacked = False
        grid = QGridLayout(self)
        grid.setContentsMargins(42, 10, 10, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self._grid = grid
        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setFont(fonts.font(13, 500))
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._label.setMinimumHeight(24)
        grid.addWidget(self._label, 0, 0)
        self._actions = QWidget(self)
        self._actions_box = QHBoxLayout(self._actions)
        self._actions_box.setSpacing(12)  # separate actions: "Move to today   Dismiss", not one phrase
        self._actions_box.setContentsMargins(-4, 0, 0, 0)
        grid.addWidget(self._actions, 0, 1, Qt.AlignmentFlag.AlignVCenter)
        self._close = IconButton("x", "Dismiss", 24, self, glyph=14)
        self._close.clicked.connect(self.dismiss)
        self._close.setVisible(dismissible)
        grid.addWidget(self._close, 0, 2, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(0, 1)
        self.setMinimumHeight(44)
        self.setAccessibleName(text)
        self.set_actions(actions or [])

    # ------------------------------------------------------------------ responsive layout
    def _want_stacked(self, width: int) -> bool:
        if not self._action_buttons:
            return False
        m = self._grid.contentsMargins()
        avail = width - m.left() - m.right()
        if not self._close.isHidden():
            avail -= self._close.width() + self._grid.horizontalSpacing()
        text_w = self._label.fontMetrics().horizontalAdvance(self._label.text())
        actions_w = self._actions.sizeHint().width()
        return text_w + self._grid.horizontalSpacing() + actions_w > avail

    def _apply_mode(self, stacked: bool) -> None:
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self._grid.removeWidget(self._actions)
        if stacked:
            self._grid.addWidget(self._actions, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        else:
            self._grid.addWidget(self._actions, 0, 1, Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        self._apply_mode(self._want_stacked(self.width()))
        super().resizeEvent(event)  # type: ignore[arg-type]

    def is_stacked(self) -> bool:
        """True when the actions sit under the text (narrow widths)."""
        return self._stacked

    # ------------------------------------------------------------------ API
    def text(self) -> str:
        """Banner text."""
        return self._label.text()

    def set_text(self, text: str) -> None:
        """Replace the text."""
        self._label.setText(text)
        self.setAccessibleName(text)
        self._apply_mode(self._want_stacked(self.width()))

    def tone(self) -> str:
        """Current tone."""
        return self._tone

    def set_tone(self, tone: str) -> None:
        """Switch tone (info/warning/success/accent/danger)."""
        self._tone = tone if tone in BANNER_TONES else "info"
        self.update()

    def set_icon(self, name: str | None) -> None:
        """Override the tone's default icon."""
        self._icon = name
        self.update()

    def set_dismissible(self, on: bool) -> None:
        """Show/hide the × button."""
        self._close.setVisible(on)

    def set_actions(self, actions: Sequence[Action]) -> None:
        """Replace the inline actions."""
        for b in self._action_buttons:
            self._actions_box.removeWidget(b)
            b.deleteLater()
        self._action_buttons = []
        for i, (label, cb) in enumerate(actions):
            btn = LinkButton(label, self, tone="accent" if i == 0 else "muted", weight=600)
            btn.clicked.connect(lambda _c=False, lab=label, fn=cb: self._run(lab, fn))
            self._actions_box.addWidget(btn)
            self._action_buttons.append(btn)
        self._actions.setVisible(bool(self._action_buttons))
        self._apply_mode(self._want_stacked(self.width()) if self.width() > 0 else False)

    def action_buttons(self) -> list[LinkButton]:
        """The action buttons (in order)."""
        return list(self._action_buttons)

    def dismiss(self) -> None:
        """Hide and emit ``dismissed``."""
        self.hide()
        self.dismissed.emit()

    def _run(self, label: str, fn: Callable[[], object]) -> None:
        self.actionTriggered.emit(label)
        fn()

    # ------------------------------------------------------------------ paint
    def _colors(self) -> tuple[QColor, QColor, QColor]:
        p = theme.palette()
        if self._tone == "warning":
            return QColor(p.warning_soft), theme.with_alpha(p.warning, 0.30), QColor(p.warning)
        if self._tone == "success":
            return QColor(p.success_soft), theme.with_alpha(p.success, 0.30), QColor(p.success)
        if self._tone == "accent":
            return QColor(p.accent_soft), theme.with_alpha(p.accent, 0.32), QColor(p.accent)
        if self._tone == "danger":
            return QColor(p.danger_soft), theme.with_alpha(p.danger, 0.30), QColor(p.danger)
        return QColor(p.surface_alt), QColor(p.border_strong), QColor(p.text_muted)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        bg, border, fg = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill_rounded(p, QRectF(self.rect()), 10.0, bg, border)
        name = self._icon or _DEFAULT_ICONS[self._tone]
        pm = icons.pixmap(name, 18, fg, self.devicePixelRatioF())
        lg = self._label.geometry()
        line_h = self._label.fontMetrics().lineSpacing()
        multi = lg.height() > line_h * 1.6 or self._stacked
        y = lg.top() + (min(lg.height(), line_h) - 18) / 2.0 if multi else (self.height() - 18) / 2.0
        p.drawPixmap(14, int(round(y)), pm)
        p.end()
