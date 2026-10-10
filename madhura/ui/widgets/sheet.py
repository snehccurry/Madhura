"""In-window modal sheets.

:class:`SheetHost` overlays its parent (the main window's central area): it dims the background
with the ``overlay`` token and shows one centred card (radius 16) at a time; further sheets queue
FIFO. Esc closes the sheet unless ``content.dismissible`` is False, Ctrl+Enter triggers the
content's primary button, focus moves into the sheet (and is trapped there) and is restored when
the last sheet closes.

:class:`SheetContent` is the base class for sheet bodies; :class:`ConfirmSheet` is the standard
confirmation.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from madhura.ui import animations, fonts, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.inputs import TextField
from madhura.ui.widgets.popup import paint_shadow

try:
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover
    def _is_valid(_obj: object) -> bool:
        return True


__all__ = ["SheetHost", "SheetContent", "ConfirmSheet", "SHEET_RADIUS", "SHEET_WIDTH", "FLOATING_PROP"]

SHEET_RADIUS = 16.0
SHEET_WIDTH = 440
_CARD_MARGIN = 24  # shadow margin around the sheet card
FLOATING_PROP = "mh_floating"  # widgets (popovers) exempt from the sheet focus trap


def _inside_floating(w: QWidget | None) -> bool:
    while w is not None:
        if w.property(FLOATING_PROP):
            return True
        w = w.parentWidget()
    return False


def sheet_width_of(content: QWidget) -> int:
    """Width requested by ``content``: an int ``width`` attribute (spec) or ``sheet_width``."""
    w = getattr(content, "width", None)
    if isinstance(w, int) and not isinstance(w, bool):
        return w
    sw = getattr(content, "sheet_width", SHEET_WIDTH)
    return int(sw) if isinstance(sw, (int, float)) else SHEET_WIDTH


class SheetContent(QWidget):
    """Base class for sheet bodies.

    Attributes: ``sheet_width`` (default 440; a plain int ``width`` class attribute is honoured
    too), ``dismissible`` (Esc closes; default True), ``close_on_outside_click`` (default False),
    ``padding`` (default 24).

    Helpers: :meth:`set_title` (title + optional subtitle), :attr:`body` (QVBoxLayout),
    :attr:`footer` (right-aligned QHBoxLayout; :attr:`footer_left` for left items),
    :meth:`add_footer_button`, :meth:`set_primary` (Ctrl+Enter), :meth:`close_sheet`.
    ``finished`` is emitted once the sheet has closed (however it was closed).
    """

    finished = Signal()

    sheet_width: int = SHEET_WIDTH
    dismissible: bool = True
    close_on_outside_click: bool = False
    padding: int = 24

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host: "SheetHost | None" = None
        self._primary: QAbstractButton | None = None
        self._title: QLabel | None = None
        self._subtitle: QLabel | None = None
        pad = self.padding
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(pad, pad, pad, pad)
        self._outer.setSpacing(0)
        self._header = QVBoxLayout()
        self._header.setSpacing(6)
        self._header.setContentsMargins(0, 0, 0, 0)
        self._outer.addLayout(self._header)
        #: Main content layout.
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        self.body.setContentsMargins(0, 0, 0, 0)
        self._outer.addLayout(self.body)
        self._footer_spacing = 24
        self._footer_row = QHBoxLayout()
        self._footer_row.setContentsMargins(0, self._footer_spacing, 0, 0)
        self._footer_row.setSpacing(8)
        #: Left side of the footer (e.g. step dots or a secondary link).
        self.footer_left = QHBoxLayout()
        self.footer_left.setSpacing(8)
        #: Right-aligned footer buttons.
        self.footer = QHBoxLayout()
        self.footer.setSpacing(8)
        self._footer_row.addLayout(self.footer_left)
        self._footer_row.addStretch(1)
        self._footer_row.addLayout(self.footer)
        self._outer.addLayout(self._footer_row)

    # ------------------------------------------------------------------ helpers
    def set_title(self, title: str, subtitle: str | None = None) -> None:
        """Sheet title (18/600) and optional muted subtitle."""
        if self._title is None:
            self._title = QLabel(self)
            self._title.setFont(fonts.font(18, 600))
            self._title.setWordWrap(True)
            self._header.addWidget(self._title)
            self._header.setContentsMargins(0, 0, 0, 16)
        self._title.setText(title)
        if subtitle is not None:
            if self._subtitle is None:
                self._subtitle = QLabel(self)
                self._subtitle.setProperty("role", "muted")
                self._subtitle.setFont(fonts.font(13))
                self._subtitle.setWordWrap(True)
                self._header.addWidget(self._subtitle)
            self._subtitle.setText(subtitle)
            self._subtitle.setVisible(bool(subtitle))

    def title_text(self) -> str:
        """Current title ("" if none)."""
        return self._title.text() if self._title is not None else ""

    def add_footer_button(self, button: QAbstractButton, primary: bool = False) -> QAbstractButton:
        """Append a footer button (right-aligned, in order). ``primary`` → Ctrl+Enter target."""
        self.footer.addWidget(button)
        if primary:
            self._primary = button
        return button

    def set_primary(self, button: QAbstractButton | None) -> None:
        """The button triggered by Ctrl+Enter / Ctrl+Return."""
        self._primary = button

    def primary_button(self) -> QAbstractButton | None:
        """The Ctrl+Enter button."""
        return self._primary

    def set_footer_visible(self, visible: bool) -> None:
        """Hide/show the footer row."""
        for lay in (self.footer, self.footer_left):
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is not None:
                    w.setVisible(visible)
        self._footer_row.setContentsMargins(0, self._footer_spacing if visible else 0, 0, 0)

    def initial_focus_widget(self) -> QWidget | None:
        """Widget focused when the sheet opens (default: first text input, else primary button)."""
        for w in self.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)) and w.isVisibleTo(self) and w.isEnabled():
                return w
        return self._primary

    def on_shown(self) -> None:
        """Hook: called after the sheet became visible."""

    def host(self) -> "SheetHost | None":
        """The SheetHost showing this content (None when not hosted)."""
        return self._host

    def close_sheet(self) -> None:
        """Close this sheet (via its host) — emits ``finished`` once closed."""
        if self._host is not None and _is_valid(self._host):
            self._host.close_sheet(self)
        else:
            self.finished.emit()


class _SheetCard(QWidget):
    """Painted rounded card (with shadow) holding the scrollable content."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        lay = QVBoxLayout(self)
        m = _CARD_MARGIN
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(0)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.scroll)
        self._content: QWidget | None = None

    def set_content(self, content: QWidget) -> None:
        self._content = content
        self.scroll.setWidget(content)
        content.setAutoFillBackground(False)  # QScrollArea.setWidget() turns it on
        content.show()

    def take_content(self) -> QWidget | None:
        c = self._content
        if c is not None:
            self.scroll.takeWidget()
        self._content = None
        return c

    def content_height(self, width: int) -> int:
        c = self._content
        if c is None:
            return 0
        lay = c.layout()
        if lay is not None:
            lay.activate()
            if lay.hasHeightForWidth():
                return lay.totalHeightForWidth(width)
            return lay.totalSizeHint().height()
        return c.sizeHint().height()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_shadow(p, QRectF(self.rect()), SHEET_RADIUS, _CARD_MARGIN, blur=40, offset_y=12)
        m = _CARD_MARGIN
        fill_rounded(p, QRectF(self.rect()).adjusted(m, m, -m, -m), SHEET_RADIUS, pal.surface, pal.border_strong if pal.is_dark else pal.border)
        p.end()


class SheetHost(QWidget):
    """Overlay that presents :class:`SheetContent` instances over ``parent``.

    Signals: ``sheetOpened(object)``, ``sheetClosed(object)``, ``activeChanged(bool)``.
    """

    sheetOpened = Signal(object)
    sheetClosed = Signal(object)
    activeChanged = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("mhSheetHost")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._queue: list[SheetContent] = []
        self._current: SheetContent | None = None
        self._closing = False
        self._progress = 0.0
        self._prev_focus: QWidget | None = None
        self._filter_installed = False
        self._card = _SheetCard(self)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_progress)
        self._anim.finished.connect(self._on_anim_finished)
        self._anim_dir = 0
        self._keep_dim = False  # overlay stays fully dimmed while chaining queued sheets
        self.hide()
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    # ------------------------------------------------------------------ public API
    def show_sheet(self, content: SheetContent) -> None:
        """Show ``content`` now, or queue it (FIFO) if a sheet is already open."""
        if content is self._current or content in self._queue:
            return
        content._host = self  # noqa: SLF001
        if self._current is not None or self._closing:
            self._queue.append(content)
            return
        self._open(content)

    def close_sheet(self, content: SheetContent | None = None) -> None:
        """Close the current sheet (or remove ``content`` from the queue)."""
        if content is not None and content in self._queue:
            self._queue.remove(content)
            content._host = None  # noqa: SLF001
            content.finished.emit()
            return
        if self._current is None or self._closing:
            return
        if content is not None and content is not self._current:
            return
        self._close_current()

    def has_sheet(self) -> bool:
        """True while a sheet is open (or closing)."""
        return self._current is not None

    def current(self) -> SheetContent | None:
        """The visible sheet content."""
        return self._current

    def queued(self) -> list[SheetContent]:
        """Contents waiting to be shown."""
        return list(self._queue)

    def relayout(self) -> None:
        """Re-fit the card to the current sheet's content (after its height changed)."""
        self._layout_card(self._progress)

    def card_geometry(self) -> QRect:
        """Geometry of the visible card (host coordinates, excluding the shadow margin)."""
        m = _CARD_MARGIN
        return self._card.geometry().adjusted(m, m, -m, -m)

    # ------------------------------------------------------------------ open / close
    def _open(self, content: SheetContent) -> None:
        self._current = content
        content._host = self  # noqa: SLF001
        self._card.set_content(content)
        par = self.parentWidget()
        if par is not None:
            self.setGeometry(par.rect())
        fw = QApplication.focusWidget()
        if self._prev_focus is None and fw is not None and not self.isAncestorOf(fw):
            self._prev_focus = fw
        self._install_filter()
        self._layout_card(0.0)
        was_active = self.isVisible()
        self.show()
        self.raise_()
        self._animate(1)
        self.sheetOpened.emit(content)
        if not was_active:
            self.activeChanged.emit(True)
        QTimer.singleShot(0, lambda: self._focus_into(content))
        content.on_shown()

    def _focus_into(self, content: SheetContent) -> None:
        if content is not self._current or not _is_valid(content):
            return
        target = content.initial_focus_widget()
        if target is not None and target.isVisible():
            target.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            first = self._focusables()
            if first:
                first[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def _close_current(self) -> None:
        self._closing = True
        self._keep_dim = bool(self._queue)
        self._animate(-1)

    def _finish_close(self) -> None:
        content = self._current
        self._card.take_content()
        self._current = None
        self._closing = False
        if content is not None and _is_valid(content):
            content._host = None  # noqa: SLF001
            content.hide()
            content.setParent(None)  # type: ignore[call-overload]
        if content is not None and _is_valid(content):
            content.finished.emit()
            self.sheetClosed.emit(content)
        if self._queue:
            self._open(self._queue.pop(0))
            return
        self._keep_dim = False
        self.hide()
        self._remove_filter()
        prev = self._prev_focus
        self._prev_focus = None
        if prev is not None and _is_valid(prev) and prev.isVisible() and prev.isEnabled():
            prev.setFocus(Qt.FocusReason.OtherFocusReason)
        self.activeChanged.emit(False)

    # ------------------------------------------------------------------ animation
    def _animate(self, direction: int) -> None:
        self._anim.stop()
        self._anim_dir = direction
        target = 1.0 if direction > 0 else 0.0
        if not animations.motion_enabled() or not self.isVisible():
            self._on_progress(target)
            self._on_anim_finished()
            return
        self._anim.setDuration(200 if direction > 0 else 160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic if direction > 0 else QEasingCurve.Type.InCubic)
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        eff = QGraphicsOpacityEffect(self._card)
        eff.setOpacity(self._progress)
        self._card.setGraphicsEffect(eff)
        self._anim.start()

    def _on_progress(self, v: Any) -> None:
        self._progress = float(v)
        eff = self._card.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(self._progress)
        self._layout_card(self._progress)
        self.update()

    def _on_anim_finished(self) -> None:
        self._card.setGraphicsEffect(None)  # never leave an effect on text
        if self._anim_dir < 0:
            self._finish_close()
        else:
            self._keep_dim = False
            self.update()

    def _layout_card(self, progress: float) -> None:
        content = self._current
        if content is None:
            return
        w = sheet_width_of(content)
        m = _CARD_MARGIN
        max_h = max(160, self.height() - 64)
        content_h = self._card.content_height(w)
        h = min(content_h, max_h)
        # Content that fits never gets a scrollbar. (Left "as needed", QScrollArea keeps a bar it
        # showed once if wrapped text grows a line in the 10 px narrower viewport.)
        policy = Qt.ScrollBarPolicy.ScrollBarAsNeeded if content_h > max_h else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        if self._card.scroll.verticalScrollBarPolicy() != policy:
            self._card.scroll.setVerticalScrollBarPolicy(policy)
        offset = 0 if not animations.motion_enabled() else int(round((1.0 - progress) * 12))
        x = (self.width() - w) // 2
        y = max(32, (self.height() - h) // 2) + offset
        self._card.setGeometry(QRect(x - m, y - m, w + 2 * m, h + 2 * m))

    # ------------------------------------------------------------------ events
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if obj is self.parentWidget():
            if et == QEvent.Type.Resize:
                self.setGeometry(self.parentWidget().rect())  # type: ignore[union-attr]
                self._layout_card(self._progress)
            return False
        if self._current is None or not self.isVisible():
            return False
        if et == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            return self._handle_key(event)
        return False

    def _handle_key(self, event: QKeyEvent) -> bool:
        if QApplication.activePopupWidget() is not None:
            return False
        fw = QApplication.focusWidget()
        if fw is not None and fw.window() is not self.window():
            return False
        if fw is not None and _inside_floating(fw):
            return False
        key = event.key()
        content = self._current
        if content is None:
            return False
        if key == Qt.Key.Key_Escape:
            if getattr(content, "dismissible", True):
                self.close_sheet()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            btn = content.primary_button()
            if btn is not None and btn.isEnabled() and btn.isVisible():
                btn.click()
                return True
            return False
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            items = self._focusables()
            if not items:
                return True
            back = key == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            cur = fw if fw in items else None
            if cur is None:
                items[0].setFocus(Qt.FocusReason.TabFocusReason)
                return True
            idx = items.index(cur)
            if back and idx == 0:
                items[-1].setFocus(Qt.FocusReason.BacktabFocusReason)
                return True
            if not back and idx == len(items) - 1:
                items[0].setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return False

    def _focusables(self) -> list[QWidget]:
        content = self._current
        if content is None:
            return []
        out: list[QWidget] = []
        w: QWidget = content
        for _ in range(2000):
            w = w.nextInFocusChain()
            if w is content or w is None:
                break
            if (
                content.isAncestorOf(w)
                and w.isVisible()
                and w.isEnabled()
                and bool(w.focusPolicy() & Qt.FocusPolicy.TabFocus)
                and w.focusProxy() is None
            ):
                out.append(w)
        return out

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        if self._current is None or new is None or not self.isVisible():
            return
        if new.window() is not self.window() or self.isAncestorOf(new) or _inside_floating(new):
            return
        items = self._focusables()
        if items:
            QTimer.singleShot(0, lambda: items[0].setFocus(Qt.FocusReason.OtherFocusReason) if _is_valid(items[0]) else None)

    def _install_filter(self) -> None:
        if self._filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.focusChanged.connect(self._on_focus_changed)  # type: ignore[attr-defined]
            self._filter_installed = True

    def _remove_filter(self) -> None:
        if not self._filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            try:
                app.focusChanged.disconnect(self._on_focus_changed)  # type: ignore[attr-defined]
            except (RuntimeError, TypeError):
                pass
        self._filter_installed = False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        event.accept()
        content = self._current
        if content is None:
            return
        if not self._card.geometry().contains(event.position().toPoint()) and getattr(content, "close_on_outside_click", False):
            if getattr(content, "dismissible", True):
                self.close_sheet()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        event.accept()

    def wheelEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        c = QColor(theme.palette().overlay)
        factor = 1.0 if self._keep_dim else max(0.0, min(1.0, self._progress))
        c.setAlphaF(c.alphaF() * factor)
        p.fillRect(self.rect(), c)
        p.end()


class ConfirmSheet(SheetContent):
    """Standard confirmation: title, text, Cancel + OK (``danger`` → solid danger button).

    ``type_to_confirm`` (e.g. "reset") requires typing that word before OK enables.
    Emits ``accepted`` or ``rejected`` exactly once.
    """

    accepted = Signal()
    rejected = Signal()

    sheet_width = 420

    def __init__(
        self,
        title: str,
        text: str = "",
        ok_text: str = "OK",
        danger: bool = False,
        type_to_confirm: str | None = None,
        parent: QWidget | None = None,
        *,
        cancel_text: str = "Cancel",
    ) -> None:
        super().__init__(parent)
        self._decided = False
        self._accepted = False
        self._phrase = type_to_confirm
        self.set_title(title)
        self.text_label = QLabel(text, self)
        self.text_label.setWordWrap(True)
        self.text_label.setFont(fonts.font(14))
        self.text_label.setProperty("role", "muted")
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.text_label.setVisible(bool(text))
        self.body.addWidget(self.text_label)
        self.confirm_field: TextField | None = None
        if type_to_confirm:
            hint = QLabel(f"Type “{type_to_confirm}” to confirm", self)
            hint.setProperty("role", "caption")
            hint.setFont(fonts.role_font("caption"))
            self.body.addSpacing(4)
            self.body.addWidget(hint)
            self.confirm_field = TextField(type_to_confirm, parent=self)
            self.confirm_field.textChanged.connect(self._sync_ok)
            self.confirm_field.returnPressed.connect(self._try_accept)
            self.body.addWidget(self.confirm_field)
        self.cancel_button = Button(cancel_text, "ghost", parent=self)
        self.ok_button = Button(ok_text, "danger_solid" if danger else "primary", parent=self)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._try_accept)
        self.add_footer_button(self.cancel_button)
        self.add_footer_button(self.ok_button, primary=True)
        self.finished.connect(self._on_finished)
        self._sync_ok()

    def _sync_ok(self, *_args: object) -> None:
        if self._phrase and self.confirm_field is not None:
            self.ok_button.setEnabled(self.confirm_field.text().strip().lower() == self._phrase.strip().lower())

    def _try_accept(self) -> None:
        if self.ok_button.isEnabled():
            self.accept()

    def accept(self) -> None:
        """Confirm and close."""
        if self._decided:
            return
        self._decided = True
        self._accepted = True
        self.accepted.emit()
        self.close_sheet()

    def reject(self) -> None:
        """Cancel and close."""
        if self._decided:
            return
        self._decided = True
        self.rejected.emit()
        self.close_sheet()

    def was_accepted(self) -> bool:
        """True once accepted."""
        return self._accepted

    def _on_finished(self) -> None:
        if not self._decided:
            self._decided = True
            self.rejected.emit()

    def initial_focus_widget(self) -> QWidget | None:
        if self.confirm_field is not None:
            return self.confirm_field.line_edit
        return self.ok_button

