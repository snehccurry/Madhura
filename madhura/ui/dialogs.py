"""Synchronous dialogs — the ONLY place UI code may block on ``exec()``.

Keep calls going through this module so tests can monkeypatch them::

    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)

``confirm`` / ``ask_text`` / ``show_error`` / ``show_info`` use a frameless modal dialog that looks
exactly like a :class:`~madhura.ui.widgets.sheet.ConfirmSheet` (sheets are asynchronous; this is
the synchronous path). File pickers use ``QFileDialog``.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QVBoxLayout, QWidget

from madhura.ui import fonts, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.inputs import TextField
from madhura.ui.widgets.popup import paint_shadow
from madhura.ui.widgets.sheet import SHEET_RADIUS, ConfirmSheet, SheetContent, sheet_width_of

__all__ = [
    "StyledDialog",
    "confirm",
    "ask_text",
    "get_save_path",
    "get_open_path",
    "show_error",
    "show_info",
]

_MARGIN = 24


class StyledDialog(QDialog):
    """Frameless modal dialog that renders a :class:`SheetContent` as a sheet-style card."""

    def __init__(self, parent: QWidget | None, content: SheetContent) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("mhStyledDialog")
        self.setModal(True)
        self.content = content
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        lay.addWidget(content)
        content.setFixedWidth(sheet_width_of(content))
        content.finished.connect(self._on_finished)
        title = content.title_text()
        if title:
            self.setWindowTitle(title)
        self.adjustSize()
        if parent is not None:
            win = parent.window()
            g = win.frameGeometry() if win.isVisible() else win.geometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def _on_finished(self) -> None:
        if self.result() == 0 and self.isVisible():
            self.reject()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if getattr(self.content, "dismissible", True):
                self.content.close_sheet()
                self.reject()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            btn = self.content.primary_button()
            if btn is not None and btn.isEnabled():
                btn.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        target = self.content.initial_focus_widget()
        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_shadow(p, QRectF(self.rect()), SHEET_RADIUS, _MARGIN, blur=40, offset_y=12)
        card = QRectF(self.rect()).adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        fill_rounded(p, card, SHEET_RADIUS, pal.surface, pal.border_strong if pal.is_dark else pal.border)
        p.end()


def confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    ok_text: str = "OK",
    danger: bool = False,
    *,
    cancel_text: str = "Cancel",
    type_to_confirm: str | None = None,
) -> bool:
    """Blocking confirmation. Returns True when the user confirms."""
    sheet = ConfirmSheet(title, text, ok_text, danger, type_to_confirm, cancel_text=cancel_text)
    dlg = StyledDialog(parent, sheet)
    sheet.accepted.connect(dlg.accept)
    sheet.rejected.connect(dlg.reject)
    return dlg.exec() == QDialog.DialogCode.Accepted and sheet.was_accepted()


class _AskTextSheet(SheetContent):
    sheet_width = 420

    def __init__(self, title: str, label: str, text: str, ok_text: str, placeholder: str) -> None:
        super().__init__()
        self.accepted_value: str | None = None
        self.set_title(title, label or None)
        self.field = TextField(placeholder, parent=self)
        self.field.set_text(text, select=True)
        self.body.addWidget(self.field)
        cancel = Button("Cancel", "ghost", parent=self)
        ok = Button(ok_text, "primary", parent=self)
        cancel.clicked.connect(self.close_sheet)
        ok.clicked.connect(self._accept)
        self.field.returnPressed.connect(self._accept)
        self.add_footer_button(cancel)
        self.add_footer_button(ok, primary=True)

    def _accept(self) -> None:
        self.accepted_value = self.field.text()
        self.close_sheet()

    def initial_focus_widget(self) -> QWidget | None:
        return self.field.line_edit


def ask_text(
    parent: QWidget | None,
    title: str,
    label: str = "",
    text: str = "",
    ok_text: str = "OK",
    placeholder: str = "",
) -> str | None:
    """Blocking single-line prompt. Returns the text, or None when cancelled."""
    sheet = _AskTextSheet(title, label, text, ok_text, placeholder)
    dlg = StyledDialog(parent, sheet)
    sheet.finished.connect(lambda: dlg.accept() if sheet.accepted_value is not None else dlg.reject())
    dlg.exec()
    return sheet.accepted_value


class _MessageSheet(SheetContent):
    sheet_width = 420

    def __init__(self, title: str, text: str, tone: str) -> None:
        super().__init__()
        self.set_title(title)
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setFont(fonts.font(14))
        label.setProperty("role", "muted")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(label)
        ok = Button("OK", "primary" if tone != "error" else "secondary", parent=self)
        ok.clicked.connect(self.close_sheet)
        self.add_footer_button(ok, primary=True)


def show_error(parent: QWidget | None, title: str, text: str) -> None:
    """Blocking error message."""
    sheet = _MessageSheet(title, text, "error")
    dlg = StyledDialog(parent, sheet)
    sheet.finished.connect(dlg.accept)
    dlg.exec()


def show_info(parent: QWidget | None, title: str, text: str) -> None:
    """Blocking informational message."""
    sheet = _MessageSheet(title, text, "info")
    dlg = StyledDialog(parent, sheet)
    sheet.finished.connect(dlg.accept)
    dlg.exec()


def get_save_path(parent: QWidget | None, title: str, default_name: str = "", filter: str = "") -> str | None:  # noqa: A002
    """Native "Save as" picker. Returns the chosen path or None."""
    path, _sel = QFileDialog.getSaveFileName(parent, title, default_name, filter)
    return path or None


def get_open_path(parent: QWidget | None, title: str, filter: str = "", directory: str = "") -> str | None:  # noqa: A002
    """Native "Open" picker. Returns the chosen path or None."""
    path, _sel = QFileDialog.getOpenFileName(parent, title, directory, filter)
    return path or None
