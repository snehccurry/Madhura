"""Card: the rounded surface every page section sits on."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from madhura.ui import fonts, theme
from madhura.ui.widgets._common import fill_rounded

__all__ = ["Card"]


class Card(QWidget):
    """Rounded surface (radius 12) with a crisp 1 px border, painted by the widget itself.

    Optional header: ``title`` (16/600), ``subtitle`` (caption) and an ``action`` widget aligned
    right. Put content into :attr:`body` (a ``QVBoxLayout``, spacing 12).

    ``background``/``border`` take palette token names (default ``surface``/``border``).
    """

    def __init__(
        self,
        title: str | None = None,
        subtitle: str | None = None,
        action: QWidget | None = None,
        padding: int | tuple[int, int, int, int] = 20,
        parent: QWidget | None = None,
        *,
        radius: float = 12.0,
        background: str = "surface",
        border: str | None = "border",
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._bg_token = background
        self._border_token = border
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        pads = (padding,) * 4 if isinstance(padding, int) else padding
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(*pads)
        self._outer.setSpacing(16)
        self._title_label: QLabel | None = None
        self._subtitle_label: QLabel | None = None
        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(8)
        self._header = QWidget(self)
        self._header.setLayout(self.header_layout)
        self._header_text = QVBoxLayout()
        self._header_text.setContentsMargins(0, 0, 0, 0)
        self._header_text.setSpacing(2)
        self.header_layout.addLayout(self._header_text, 1)
        self._action: QWidget | None = None
        self._outer.addWidget(self._header)
        self._header.setVisible(False)
        #: Content layout.
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        self._outer.addLayout(self.body)
        if title:
            self.set_title(title)
        if subtitle:
            self.set_subtitle(subtitle)
        if action is not None:
            self.set_action(action)

    # ------------------------------------------------------------------ header API
    def set_title(self, text: str | None) -> None:
        """Set/clear the card title."""
        if text is None:
            if self._title_label is not None:
                self._title_label.hide()
            self._sync_header()
            return
        if self._title_label is None:
            self._title_label = QLabel(self._header)
            self._title_label.setProperty("role", "h2")
            self._title_label.setFont(fonts.role_font("h2"))
            self._header_text.insertWidget(0, self._title_label)
        self._title_label.setText(text)
        self._title_label.show()
        self._sync_header()

    def title_label(self) -> QLabel | None:
        """The title QLabel (None when no title was set)."""
        return self._title_label

    def set_subtitle(self, text: str | None) -> None:
        """Set/clear the caption under the title."""
        if text is None:
            if self._subtitle_label is not None:
                self._subtitle_label.hide()
            self._sync_header()
            return
        if self._subtitle_label is None:
            self._subtitle_label = QLabel(self._header)
            self._subtitle_label.setProperty("role", "caption")
            self._subtitle_label.setFont(fonts.role_font("caption"))
            self._subtitle_label.setWordWrap(True)
            self._header_text.addWidget(self._subtitle_label)
        self._subtitle_label.setText(text)
        self._subtitle_label.show()
        self._sync_header()

    def set_action(self, widget: QWidget | None) -> None:
        """Place ``widget`` at the right of the header (replacing any previous action)."""
        if self._action is not None:
            self.header_layout.removeWidget(self._action)
            self._action.setParent(None)  # type: ignore[call-overload]
        self._action = widget
        if widget is not None:
            self.header_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync_header()

    def add_header_widget(self, widget: QWidget) -> None:
        """Insert an extra widget into the header row (before the action)."""
        idx = self.header_layout.count() - (1 if self._action is not None else 0)
        self.header_layout.insertWidget(idx, widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync_header()

    def _sync_header(self) -> None:
        visible = any(
            w is not None and not w.isHidden() for w in (self._title_label, self._subtitle_label, self._action)
        )
        self._header.setVisible(visible)

    def set_padding(self, left: int, top: int | None = None, right: int | None = None, bottom: int | None = None) -> None:
        """Change the inner padding (one value → all sides)."""
        top = left if top is None else top
        right = left if right is None else right
        bottom = top if bottom is None else bottom
        self._outer.setContentsMargins(left, top, right, bottom)

    def set_background(self, token: str, border: str | None = "border") -> None:
        """Change fill/border palette tokens (e.g. ``surface_alt``)."""
        self._bg_token = token
        self._border_token = border
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        bg = getattr(pal, self._bg_token, pal.surface)
        border = getattr(pal, self._border_token) if self._border_token else None
        fill_rounded(p, QRectF(self.rect()), self._radius, bg, border)
        p.end()
