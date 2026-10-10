"""ShortcutsSheet — every keyboard shortcut at a glance (spec §1.13).

Opened with ``?`` / ``Ctrl+/`` from anywhere in the main window, and from Settings → About.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from madhura.ui import fonts
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.labels import Kbd, Label, Micro
from madhura.ui.widgets.sheet import SheetContent

__all__ = ["ShortcutsSheet", "SHORTCUT_GROUPS", "display_keys"]

#: (group title, ((description, keys), ...)) — keys use "Ctrl"; macOS shows "⌘".
SHORTCUT_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "General",
        (
            ("Focus", "Ctrl+1"),
            ("Habits", "Ctrl+2"),
            ("Insights", "Ctrl+3"),
            ("Settings", "Ctrl+,"),
            ("New task · new habit", "Ctrl+N"),
            ("Hide to mini timer", "Ctrl+H"),
            ("Keyboard shortcuts", "?"),
            ("Quit Madhura", "Ctrl+Q"),
        ),
    ),
    (
        "Focus session",
        (
            ("Lock in", "Ctrl+Enter"),
            ("Pause · resume", "Space"),
            ("Finish session", "Ctrl+Shift+F"),
            ("Capture a stray thought", "C"),
            ("Check-in: Focused", "F"),
            ("Check-in: Focusing back", "B"),
            ("Close · cancel", "Esc"),
        ),
    ),
)

_ROW_HEIGHT = 32


def display_keys(keys: str) -> str:
    """Platform display form of a shortcut ("Ctrl+N" → "⌘+N" on macOS)."""
    if sys.platform == "darwin":
        return keys.replace("Ctrl", "⌘")
    return keys


class _ShortcutRow(QWidget):
    """Description on the left, key caps on the right."""

    def __init__(self, text: str, keys: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_ROW_HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        label = Label(text, "body", self)
        label.setFont(fonts.font(13))
        lay.addWidget(label, 1)
        self.kbd = Kbd(display_keys(keys), self)
        lay.addWidget(self.kbd, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setAccessibleName(f"{text}: {display_keys(keys)}")


class ShortcutsSheet(SheetContent):
    """Two columns of shortcut rows (General · Focus session) with key caps."""

    sheet_width = 640

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_title("Keyboard shortcuts")
        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(40)
        self.rows: list[_ShortcutRow] = []
        for title, items in SHORTCUT_GROUPS:
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            col.addWidget(Micro(title, self))
            col.addSpacing(6)
            for text, keys in items:
                row = _ShortcutRow(text, keys, self)
                self.rows.append(row)
                col.addWidget(row)
            col.addStretch(1)
            columns.addLayout(col, 1)
        self.body.addLayout(columns)
        done = Button("Done", "primary", parent=self)
        done.clicked.connect(self.close_sheet)
        self.add_footer_button(done, primary=True)
