"""ListRow: a hoverable row with leading / content / trailing slots."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from madhura.ui import theme
from madhura.ui.widgets._common import HoverFader, fill_rounded
from madhura.ui.widgets.labels import ElidedLabel

__all__ = ["ListRow"]


class ListRow(QWidget):
    """Row with ``leading_layout`` | ``content_layout`` (stretch) | ``trailing_layout``.

    * hover paints a soft background; ``set_selected(True)`` keeps it (keyboard selection);
    * trailing widgets added with ``hover_only=True`` keep their space but are only visible while
      the row is hovered or selected (or pinned via :meth:`set_actions_pinned`);
    * ``separator=True`` draws a 1 px divider at the bottom;
    * signals: ``clicked`` (single click / Space when focusable), ``activated`` (double-click /
      Enter), ``hoveredChanged(bool)``.
    """

    clicked = Signal()
    activated = Signal()
    hoveredChanged = Signal(bool)

    def __init__(
        self,
        height: int | None = 40,
        parent: QWidget | None = None,
        *,
        separator: bool = False,
        clickable: bool = True,
        padding: tuple[int, int] = (12, 8),
        radius: float = 8.0,
        focusable: bool = False,
    ) -> None:
        super().__init__(parent)
        self._selected = False
        self._hovered = False
        self._pinned = False
        self._separator = separator
        self._clickable = clickable
        self._radius = radius
        self._hover_only: list[QWidget] = []
        self._hover = HoverFader(self, 120)
        self._title: ElidedLabel | None = None
        self._subtitle: ElidedLabel | None = None
        if height:
            self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed if height else QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        if focusable:
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(padding[0], 0, padding[1], 0)
        lay.setSpacing(12)
        self.leading_layout = QHBoxLayout()
        self.leading_layout.setSpacing(8)
        self.leading_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(2)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.trailing_layout = QHBoxLayout()
        self.trailing_layout.setSpacing(6)
        self.trailing_layout.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.leading_layout)
        lay.addLayout(self.content_layout, 1)
        lay.addLayout(self.trailing_layout)
        self._layout = lay

    # ------------------------------------------------------------------ slots
    def add_leading(self, widget: QWidget) -> QWidget:
        """Append a widget to the leading slot."""
        self.leading_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def add_content(self, widget: QWidget) -> QWidget:
        """Append a widget to the content column."""
        self.content_layout.addWidget(widget)
        return widget

    def add_trailing(self, widget: QWidget, hover_only: bool = False) -> QWidget:
        """Append a widget to the trailing slot (optionally visible only on hover/selection)."""
        self.trailing_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        if hover_only:
            sp = widget.sizePolicy()
            sp.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(sp)
            self._hover_only.append(widget)
            widget.setVisible(self._actions_visible())
        return widget

    def set_title(self, text: str, role: str = "label") -> ElidedLabel:
        """Convenience: elided title label in the content column (created on first call)."""
        if self._title is None:
            self._title = ElidedLabel(text, role, self)
            self.content_layout.insertWidget(0, self._title)
        else:
            self._title.set_text(text)
        return self._title

    def set_subtitle(self, text: str | None) -> ElidedLabel | None:
        """Convenience: elided caption under the title (None hides it)."""
        if text is None:
            if self._subtitle is not None:
                self._subtitle.hide()
            return self._subtitle
        if self._subtitle is None:
            self._subtitle = ElidedLabel(text, "caption", self)
            self.content_layout.addWidget(self._subtitle)
        else:
            self._subtitle.set_text(text)
        self._subtitle.show()
        return self._subtitle

    @property
    def title_label(self) -> ElidedLabel | None:
        """Title label created by :meth:`set_title`."""
        return self._title

    @property
    def subtitle_label(self) -> ElidedLabel | None:
        """Caption label created by :meth:`set_subtitle`."""
        return self._subtitle

    # ------------------------------------------------------------------ state
    def is_selected(self) -> bool:
        """Selection state."""
        return self._selected

    def set_selected(self, selected: bool) -> None:
        """Mark the row selected (keyboard cursor)."""
        if selected != self._selected:
            self._selected = bool(selected)
            self._sync_actions()
            self.update()

    def is_hovered(self) -> bool:
        """True while the pointer is over the row."""
        return self._hovered

    def set_separator(self, on: bool) -> None:
        """Toggle the bottom divider."""
        self._separator = bool(on)
        self.update()

    def set_actions_pinned(self, pinned: bool) -> None:
        """Keep hover-only actions visible (e.g. while their menu is open)."""
        self._pinned = bool(pinned)
        self._sync_actions()

    def _actions_visible(self) -> bool:
        return self._hovered or self._selected or self._pinned

    def _sync_actions(self) -> None:
        vis = self._actions_visible()
        for w in self._hover_only:
            w.setVisible(vis)

    # ------------------------------------------------------------------ events
    def enterEvent(self, event: object) -> None:  # noqa: N802
        self._hovered = True
        self._hover.set_target(1.0)
        self._sync_actions()
        self.hoveredChanged.emit(True)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hovered = False
        self._hover.set_target(0.0)
        self._sync_actions()
        self.hoveredChanged.emit(False)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._clickable
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            event.accept()
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.activated.emit()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: object) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)  # type: ignore[arg-type]

    def focusOutEvent(self, event: object) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        if self._selected:
            fill_rounded(p, r, self._radius, pal.hover)
            if self.hasFocus():
                fill_rounded(p, r, self._radius, None, theme.with_alpha(pal.accent, 0.45), 1.0)
        elif self._hover.value > 0:
            fill_rounded(p, r, self._radius, theme.with_alpha(pal.hover, 0.6 * self._hover.value))
        if self._separator and not self._selected and self._hover.value < 0.5:
            m = self._layout.contentsMargins()
            p.setPen(QColor(pal.border))
            y = self.height() - 1
            p.drawLine(m.left(), y, self.width() - m.right(), y)
        p.end()
