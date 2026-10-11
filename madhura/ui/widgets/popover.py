"""Popover: a small in-window floating card anchored under a widget."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from madhura.ui import animations, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.popup import paint_shadow
from madhura.ui.widgets.sheet import FLOATING_PROP

try:
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover
    def _is_valid(_obj: object) -> bool:
        return True


__all__ = ["Popover"]

_MARGIN = 12
_RADIUS = 12.0


class Popover(QWidget):
    """Floating card (child of the anchor's window) positioned below ``anchor`` (flips above
    when there is no room). Closes on outside click (a click on the anchor closes it without
    re-triggering) or Esc, restoring focus to the anchor, and when the anchor is hidden (page
    switch, view swap, window hidden). Follows the anchor when the window is resized or the
    anchor moves (layout change, scrolling). Emits ``closed``.
    """

    closed = Signal()

    def __init__(
        self,
        anchor: QWidget,
        content: QWidget,
        *,
        align: str = "left",
        offset: int = 6,
        padding: int = 12,
        anchor_rect: QRect | None = None,
    ) -> None:
        window = anchor.window()
        super().__init__(window)
        self.setProperty(FLOATING_PROP, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._anchor = anchor
        self._anchor_rect = QRect(anchor_rect) if anchor_rect is not None else None
        self._align = align
        self._offset = offset
        self._content = content
        self._open = False
        self._reposition_pending = False
        self._prev_focus: QWidget | None = None
        lay = QVBoxLayout(self)
        m = _MARGIN + padding
        lay.setContentsMargins(m, m, m, m)
        lay.addWidget(content)
        self.hide()

    def content(self) -> QWidget:
        """The content widget."""
        return self._content

    def is_open(self) -> bool:
        """True while shown."""
        return self._open

    def set_anchor_rect(self, rect: QRect | None) -> None:
        """Anchor to a sub-rect of the anchor widget (e.g. one chip of a ChipGroup)."""
        self._anchor_rect = QRect(rect) if rect is not None else None

    # ------------------------------------------------------------------ open / close
    def open(self) -> None:
        """Show the popover."""
        if self._open:
            return
        self._open = True
        self._prev_focus = QApplication.focusWidget()
        self.adjustSize()
        self._position()
        self.show()
        self.raise_()
        if animations.motion_enabled():
            animations.fade_in(self, 140)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._focus_content()

    def dismiss(self) -> None:
        """Hide the popover and emit ``closed``."""
        if not self._open:
            return
        self._open = False
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        prev = self._prev_focus if self._prev_focus is not None else self._anchor
        self._prev_focus = None
        if prev is not None and not (_is_valid(prev) and prev.isVisible()):
            prev = None
        fw = QApplication.focusWidget()
        if prev is None and fw is not None and (fw is self or self.isAncestorOf(fw)):
            # The anchor went away with the focus inside: hand focus to what is left of the
            # anchor's chain before hiding — Qt would pass it on to the next Tab stop instead.
            holder = self._anchor if _is_valid(self._anchor) else None
            while holder is not None and not holder.isVisible():
                holder = holder.parentWidget()
            if holder is not None:
                holder.setFocus(Qt.FocusReason.OtherFocusReason)
        self.hide()
        if prev is not None:
            prev.setFocus(Qt.FocusReason.OtherFocusReason)
        self.closed.emit()

    def close(self) -> bool:  # type: ignore[override]
        """Alias of :meth:`dismiss` (keeps QWidget.close() semantics: returns True)."""
        self.dismiss()
        return True

    def _focus_content(self) -> None:
        for w in [self._content, *self._content.findChildren(QWidget)]:
            if w.isVisibleTo(self) and w.isEnabled() and w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.focusProxy() is None:
                w.setFocus(Qt.FocusReason.OtherFocusReason)
                return

    def _position(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        a = self._anchor
        rect = self._anchor_rect if self._anchor_rect is not None else a.rect()
        top_left = a.mapTo(parent, rect.topLeft())
        anchor = QRect(top_left, rect.size())
        size = self.sizeHint()
        self.resize(size)
        if self._align == "right":
            x = anchor.right() + 1 - size.width() + _MARGIN
        elif self._align == "center":
            x = anchor.center().x() - size.width() // 2
        else:
            x = anchor.left() - _MARGIN
        y = anchor.bottom() + 1 + self._offset - _MARGIN
        if y + size.height() > parent.height() and anchor.top() - self._offset - size.height() + _MARGIN >= 0:
            y = anchor.top() - self._offset - size.height() + _MARGIN
        x = max(-_MARGIN + 8, min(x, parent.width() - size.width() + _MARGIN - 8))
        y = max(-_MARGIN + 8, y)
        self.move(QPoint(x, y))

    def _schedule_position(self) -> None:
        # Not right away: application event filters see a Resize before the layouts react to
        # it, so the anchor's geometry is still the old one at that point.
        if not self._reposition_pending:
            self._reposition_pending = True
            QTimer.singleShot(0, self, self._deferred_position)

    def _deferred_position(self) -> None:
        self._reposition_pending = False
        if self._open and _is_valid(self._anchor):
            self._position()

    def _on_anchor_chain(self, obj: QObject) -> bool:
        """True if *obj* is the anchor or one of its ancestors (the window included)."""
        anchor = self._anchor
        return isinstance(obj, QWidget) and obj is not self and (obj is anchor or obj.isAncestorOf(anchor))

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._open:
            return False
        if not _is_valid(self._anchor):
            self.dismiss()
            return False
        et = event.type()
        if et == QEvent.Type.Hide:
            # Page switch, idle/active swap, hidden to the tray: the anchor went away, so does the
            # popover. (A minimize is a spontaneous hide that leaves the anchor "visible".)
            if self._on_anchor_chain(obj) and not self._anchor.isVisible():
                self.dismiss()
            return False
        if et in (QEvent.Type.Resize, QEvent.Type.Move):
            if self._on_anchor_chain(obj):
                self._schedule_position()
            return False
        if et == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget):
            if obj is self or self.isAncestorOf(obj):
                return False
            if obj.window() is not self.window():
                return False  # e.g. a completer popup or menu opened from the content
            on_anchor = obj is self._anchor or self._anchor.isAncestorOf(obj)
            self.dismiss()
            return on_anchor  # swallow the click that would re-open it
        if et == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Escape:
            if QApplication.activePopupWidget() is None:
                self.dismiss()
                return True
        return False

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_shadow(p, QRectF(self.rect()), _RADIUS, _MARGIN, blur=20, offset_y=6)
        card = QRectF(self.rect()).adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        fill_rounded(p, card, _RADIUS, pal.panel, pal.panel_border)
        p.end()
