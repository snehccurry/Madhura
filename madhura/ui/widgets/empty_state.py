"""EmptyState: icon medallion, title, body and an optional call to action."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from madhura.ui import fonts, icons, theme

__all__ = ["EmptyState", "IconMedallion", "balanced_width"]

BODY_MAX_WIDTH = 400


def balanced_width(text: str, font: QFont, max_width: int) -> int:
    """Narrowest width (≤ *max_width*) at which *text* wraps into as few lines as at
    *max_width* — centred copy then never ends in a one-word widow ("… appear" / "here.")."""
    fm = QFontMetrics(font)
    flags = int(Qt.TextFlag.TextWordWrap)

    def lines(width: int) -> int:
        return round(fm.boundingRect(QRect(0, 0, width, 100_000), flags, text).height() / max(1, fm.lineSpacing()))

    count = lines(max_width)
    if count <= 1:
        return max_width
    lo, hi = max(1, max_width // (count + 1)), max_width
    while lo < hi:
        mid = (lo + hi) // 2
        if lines(mid) <= count:
            hi = mid
        else:
            lo = mid + 1
    return min(max_width, hi + 4)  # a little slack for QLabel's own layout


class IconMedallion(QWidget):
    """Soft accent circle with a centred icon."""

    def __init__(self, icon: str, diameter: int = 56, parent: QWidget | None = None, *, tone: str = "accent") -> None:
        super().__init__(parent)
        if not icons.has(icon):
            raise KeyError(icon)
        self._icon = icon
        self._tone = tone
        self.setFixedSize(diameter, diameter)

    def set_icon(self, name: str) -> None:
        """Change the glyph."""
        if not icons.has(name):
            raise KeyError(name)
        self._icon = name
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._tone == "success":
            bg, fg = pal.success_soft, pal.success
        elif self._tone == "neutral":
            bg, fg = pal.surface_alt, pal.text_muted
        else:
            bg, fg = pal.accent_soft, pal.accent
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawEllipse(QRectF(self.rect()))
        g = int(self.width() * 0.43)
        pm = icons.pixmap(self._icon, g, fg, self.devicePixelRatioF())
        p.drawPixmap((self.width() - g) // 2, (self.height() - g) // 2, pm)
        p.end()


class EmptyState(QWidget):
    """Centred empty state. ``action`` is typically a primary :class:`Button`.

    Extra widgets (e.g. suggestion chips) can be added with :meth:`add_widget`.
    """

    def __init__(
        self,
        icon: str = "sparkle",
        title: str = "",
        body: str = "",
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 32, 24, 32)
        lay.setSpacing(0)
        self.medallion = IconMedallion(icon, 56, self)
        lay.addWidget(self.medallion, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(18)
        self._title = QLabel(title, self)
        self._title.setProperty("role", "h2")
        self._title.setFont(fonts.role_font("h2"))
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._title, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(6)
        self._body = QLabel(body, self)
        self._body.setProperty("role", "muted")
        self._body.setFont(fonts.font(13))
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._body.setMaximumWidth(balanced_width(body, self._body.font(), BODY_MAX_WIDTH))
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._body.setVisible(bool(body))
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.addStretch(1)
        body_row.addWidget(self._body, 100)
        body_row.addStretch(1)
        lay.addLayout(body_row)
        self._extra = QVBoxLayout()
        self._extra.setContentsMargins(0, 0, 0, 0)
        self._extra.setSpacing(12)
        lay.addSpacing(20)
        lay.addLayout(self._extra)
        self._action: QWidget | None = None
        self._layout = lay
        lay.insertStretch(0, 1)
        lay.addStretch(1)
        if action is not None:
            self.set_action(action)

    def set_title(self, text: str) -> None:
        """Change the title."""
        self._title.setText(text)

    def set_body(self, text: str) -> None:
        """Change the body text."""
        self._body.setText(text)
        self._body.setMaximumWidth(balanced_width(text, self._body.font(), BODY_MAX_WIDTH))
        self._body.setVisible(bool(text))

    def set_icon(self, name: str) -> None:
        """Change the medallion icon."""
        self.medallion.set_icon(name)

    def add_widget(self, widget: QWidget, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter) -> QWidget:
        """Add an extra widget below the body (above the action)."""
        self._extra.addWidget(widget, 0, align)
        return widget

    def set_action(self, widget: QWidget | None) -> None:
        """Set the call-to-action widget (centred, last)."""
        if self._action is not None:
            self._extra.removeWidget(self._action)
            self._action.setParent(None)  # type: ignore[call-overload]
        self._action = widget
        if widget is not None:
            self._extra.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
