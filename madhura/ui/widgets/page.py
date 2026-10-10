"""Page scaffolding: :class:`PageScroll` (transparent scroll area with a centred column) and
:class:`PageHeader`."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from madhura.ui import fonts

__all__ = ["PageScroll", "PageHeader"]

PAGE_MARGINS = (32, 28, 32, 32)  # left, top, right, bottom


class PageScroll(QScrollArea):
    """Transparent, frameless vertical scroll area whose content is a centred column of at most
    ``max_width`` px. Add widgets to :attr:`content_layout` (top-aligned, spacing 16)."""

    def __init__(
        self,
        max_width: int = 1040,
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = PAGE_MARGINS,
        spacing: int = 16,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QWidget()
        inner.setObjectName("mhPageInner")
        inner.setAutoFillBackground(False)
        row = QHBoxLayout(inner)
        row.setContentsMargins(*margins)
        row.setSpacing(0)
        #: The centred column widget.
        self.column = QWidget(inner)
        self.column.setMaximumWidth(max_width)
        self.column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addStretch(1)
        row.addWidget(self.column, 1000)
        row.addStretch(1)
        #: Vertical layout of the page content.
        self.content_layout = QVBoxLayout(self.column)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(spacing)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(inner)
        inner.setAutoFillBackground(False)  # QScrollArea.setWidget() turns it on

    def set_max_width(self, px: int) -> None:
        """Change the column's maximum width."""
        self.column.setMaximumWidth(px)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """Append ``widget`` to the column."""
        self.content_layout.addWidget(widget, stretch)
        return widget

    def scroll_to_top(self) -> None:
        """Scroll back to the top."""
        self.verticalScrollBar().setValue(0)


class PageHeader(QWidget):
    """Page title row: h1 (+ optional caption under it) on the left, actions on the right."""

    def __init__(
        self,
        title: str,
        actions: Sequence[QWidget] | None = None,
        parent: QWidget | None = None,
        *,
        subtitle: str | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self._title = QLabel(title, self)
        self._title.setProperty("role", "h1")
        self._title.setFont(fonts.role_font("h1"))
        col.addWidget(self._title)
        self._subtitle = QLabel(subtitle or "", self)
        self._subtitle.setProperty("role", "caption")
        self._subtitle.setFont(fonts.role_font("caption"))
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        col.addWidget(self._subtitle)
        lay.addLayout(col, 1)
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(8)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.actions_layout)
        for w in actions or []:
            self.add_action(w)
        self.setMinimumHeight(48)

    @property
    def title_label(self) -> QLabel:
        """The h1 label."""
        return self._title

    @property
    def subtitle_label(self) -> QLabel:
        """The caption label."""
        return self._subtitle

    def set_title(self, text: str) -> None:
        """Change the title."""
        self._title.setText(text)

    def set_subtitle(self, text: str | None) -> None:
        """Set/clear the caption."""
        self._subtitle.setText(text or "")
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        """Append a right-aligned action widget."""
        self.actions_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget
