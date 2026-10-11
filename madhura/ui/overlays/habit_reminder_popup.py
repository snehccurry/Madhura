"""HabitReminderPopup — the due-habit reminder panel (spec §1.8, §7.3).

A non-activating :class:`FloatingPanel` (stack priority 3, "habit"). Caption "Daily habit" for one
habit or "N habits due" for several; up to three rows, each with the habit's emoji + name, its
status ("Due now" / "15 min late" / "2 hr late" — muted or warning, never danger) and the actions
Done · Snooze 15m · Skip today. More than three → "+N more · Open habits". The ✕ closes the panel,
which the owning :class:`~madhura.services.habit_reminders.HabitReminderService` treats as
"snooze 15 min" for every row shown.

The panel only *reports* what the user clicked (:attr:`actioned`); the service writes the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from madhura.ui import fonts, theme
from madhura.ui.pages._habit_kit import HabitGlyph
from madhura.ui.widgets.buttons import Button, IconButton, LinkButton
from madhura.ui.widgets.labels import ElidedLabel, Label, Micro
from madhura.ui.widgets.popup import PRIORITIES, FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["HabitReminderPopup", "ReminderItem", "MAX_ROWS", "CONFIRM_MS", "caption_for"]

#: Rows shown at once; the rest wait behind "+N more".
MAX_ROWS = 3
#: How long the "✓ Done" confirmation stays before the panel closes.
CONFIRM_MS = 1200
_WARN_FROM_MINUTES = 15


@dataclass
class ReminderItem:
    """One due habit as shown in the panel."""

    habit_id: int
    name: str
    emoji: str | None = None
    #: "Due now" / "15 min late" / "2 hr late".
    label: str = "Due now"
    minutes_late: int = 0
    #: Muted detail before the status, e.g. "After coffee · 08:00".
    detail: str = ""
    #: Logical date the reminder is for (the log goes there).
    day: date | None = None
    urgent: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tone(self) -> str:
        """"muted" while (nearly) on time, "warning" once noticeably late — never danger."""
        return "warning" if self.minutes_late >= _WARN_FROM_MINUTES else "muted"


def caption_for(total: int) -> str:
    """"Daily habit" for one, "N habits due" for more."""
    return "Daily habit" if total <= 1 else f"{total} habits due"


def _hbox(spacing: int = 0) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


def _vbox(parent: QWidget | None = None, spacing: int = 0) -> QVBoxLayout:
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


class _Divider(QWidget):
    """1 px hairline in the border colour."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.palette().border_strong if theme.palette().is_dark else theme.palette().border))
        p.end()


class _ReminderRow(QWidget):
    """One habit: glyph, name, detail + status and the three actions.

    ``compact`` rows (several habits) use a 20 px emoji and small buttons; the single-habit row
    is roomier (32 px glyph medallion, 15/600 name, regular buttons).
    """

    actioned = Signal(int, str)

    def __init__(self, item: ReminderItem, compact: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.compact = compact
        size = "sm" if compact else "md"
        v = _vbox(self, 10 if compact else 14)
        top = _hbox(10 if compact else 12)
        self.glyph = HabitGlyph(item.emoji, item.name, 24 if compact else 36, self, emoji_px=18 if compact else 26)
        top.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop if not compact else Qt.AlignmentFlag.AlignVCenter)
        texts = _vbox(spacing=2)
        self.name_label = ElidedLabel(item.name, "habit_name", self)
        self.name_label.setFont(fonts.font(13 if compact else 15, 600))
        self.name_label.setAccessibleName(item.name)
        texts.addWidget(self.name_label)
        status_row = _hbox(0)
        self.detail_label = Label("", "caption", self)
        self.status_label = Label(item.label, "caption", self, tone=item.tone)
        status_row.addWidget(self.detail_label)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        texts.addLayout(status_row)
        top.addLayout(texts, 1)
        v.addLayout(top)

        buttons = _hbox(8)
        if compact:
            buttons.addSpacing(24 + 10)  # align with the name
        self.done_button = Button("Done", "primary", icon="check", size=size, parent=self)
        self.snooze_button = Button("Snooze 15m", "secondary", size=size, parent=self)
        self.skip_button = Button("Skip today", "ghost", size=size, parent=self)
        self.done_button.setToolTip(f"Mark ‘{item.name}’ done for today")
        self.snooze_button.setToolTip("Remind me again in 15 minutes")
        self.skip_button.setToolTip("Skip it today — your streak stays safe")
        for btn in (self.done_button, self.snooze_button, self.skip_button):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        v.addLayout(buttons)
        self.done_button.clicked.connect(lambda: self.actioned.emit(self.item.habit_id, "done"))
        self.snooze_button.clicked.connect(lambda: self.actioned.emit(self.item.habit_id, "snooze"))
        self.skip_button.clicked.connect(lambda: self.actioned.emit(self.item.habit_id, "skip"))
        self.set_item(item)

    def set_item(self, item: ReminderItem) -> None:
        """Refresh texts (lateness changes as time passes)."""
        self.item = item
        self.glyph.set_habit(item.emoji, item.name)
        self.name_label.set_text(item.name)
        self.detail_label.setText(f"{item.detail} · " if item.detail else "")
        self.detail_label.setVisible(bool(item.detail))
        self.status_label.setText(item.label)
        self.status_label.set_tone(item.tone)
        self.setAccessibleName(f"{item.name}, {item.label}")


class _Confirmation(QWidget):
    """"✓ <text>" shown briefly after the last habit was marked done."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = _hbox(10)
        self.setLayout(lay)
        self.mark = Label("✓", "habit_check", self, tone="success")
        self.mark.setFont(fonts.font(16, 700))
        self.text = Label(text, "habit_confirm", self)
        self.text.setFont(fonts.font(14, 600))
        self.text.setWordWrap(True)
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.text, 1)
        self.setMinimumHeight(28)


class HabitReminderPopup(FloatingPanel):
    """Due-habit reminder panel (stack priority 3). See the module docstring.

    Signals: ``actioned(habit_id, action)`` with action "done" | "snooze" | "skip";
    ``open_requested()`` from the "Open habits" link. ``closed`` (inherited) fires however the
    panel went away — the service decides what that means.
    """

    actioned = Signal(int, str)
    open_requested = Signal()
    close_clicked = Signal()

    def __init__(self, ctx: AppContext | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__("habit")
        self.priority = PRIORITIES["habit"]
        self._ctx = ctx
        self._items: list[ReminderItem] = []
        self._total = 0
        self._rows: list[_ReminderRow] = []
        self._confirming = False
        self.setAccessibleName("Habit reminder")

        page = QWidget()
        self.page = page
        v = _vbox(page, 0)
        header = _hbox(8)
        self.caption = Micro(caption_for(1), page)
        header.addWidget(self.caption, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.close_button = IconButton("x", "Close · snooze 15 min", 24, page, glyph=14, accessible_name="Close and snooze")
        self.close_button.clicked.connect(self._on_close_clicked)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(header)
        v.addSpacing(10)
        self.rows_box = QWidget(page)
        self.rows_layout = _vbox(self.rows_box, 12)
        v.addWidget(self.rows_box)
        self.more_row = QWidget(page)
        more = _hbox(6)
        self.more_row.setLayout(more)
        self.more_label = Label("", "caption", self.more_row)
        self.open_link = LinkButton("Open habits", self.more_row, tone="accent", size_px=12)
        self.open_link.clicked.connect(self.open_requested)
        more.addWidget(self.more_label)
        more.addWidget(self.open_link)
        more.addStretch(1)
        v.addWidget(self.more_row)
        self.more_row.hide()
        self.confirm_box = _Confirmation("", page)
        self.confirm_box.hide()
        v.addWidget(self.confirm_box)
        self.set_content(page, animate=False)
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.timeout.connect(self._finish_confirmation)

    # ------------------------------------------------------------------ content
    def items(self) -> list[ReminderItem]:
        """Items currently listed (at most :data:`MAX_ROWS`)."""
        return list(self._items[:MAX_ROWS])

    def rows(self) -> list[_ReminderRow]:
        """The row widgets (tests)."""
        return list(self._rows)

    def total(self) -> int:
        """All due habits, including those behind "+N more"."""
        return self._total

    def caption_text(self) -> str:
        """The caption as given (before the Micro label upper-cases it)."""
        return caption_for(self._total)

    def is_confirming(self) -> bool:
        """True while the "✓ Done" confirmation is showing."""
        return self._confirming

    def set_items(self, items: list[ReminderItem], total: int | None = None) -> None:
        """Show *items* (first :data:`MAX_ROWS`); *total* counts all due habits (default len)."""
        self._items = list(items)
        self._total = max(len(self._items), int(total) if total is not None else len(self._items))
        if self._confirming and self._items:
            self._confirm_timer.stop()
            self._confirming = False
            self.confirm_box.hide()
        shown = self._items[:MAX_ROWS]
        compact = self._total > 1
        same = [r.item.habit_id for r in self._rows] == [i.habit_id for i in shown] and all(r.compact == compact for r in self._rows)
        if same:
            for row, item in zip(self._rows, shown, strict=False):
                row.set_item(item)
        else:
            self._rebuild(shown, compact)
        self.caption.setText(caption_for(self._total))
        extra = self._total - len(shown)
        self.more_label.setText(f"+{extra} more ·" if extra > 0 else "")
        self.more_row.setVisible(extra > 0)
        self.rows_box.setVisible(bool(shown))
        self.close_button.setVisible(bool(shown))
        self.setAccessibleDescription(", ".join(f"{i.name} {i.label}" for i in shown))
        self.disable_child_focus()

    def _rebuild(self, shown: list[ReminderItem], compact: bool) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)  # type: ignore[call-overload]
                w.deleteLater()
        self._rows = []
        for i, item in enumerate(shown):
            if i:
                self.rows_layout.addWidget(_Divider(self.rows_box))
            row = _ReminderRow(item, compact, self.rows_box)
            row.actioned.connect(self.actioned)
            self.rows_layout.addWidget(row)
            self._rows.append(row)
        if self.more_row.isVisible() or self._total > len(shown):
            self.rows_layout.addSpacing(2)

    # ------------------------------------------------------------------ presenting
    def present(self, priority: int | None = None) -> None:
        """Push to the popup stack (habit priority)."""
        super().present(PRIORITIES["habit"] if priority is None else priority)

    def show_confirmation(self, text: str, ms: int = CONFIRM_MS) -> None:
        """Replace the rows by "✓ *text*" and close after *ms*."""
        self._confirming = True
        self._items = []
        self._total = 0
        self._rebuild([], False)
        self.rows_box.hide()
        self.more_row.hide()
        self.close_button.hide()
        self.caption.setText("Daily habit")
        self.confirm_box.text.setText(text)
        self.confirm_box.show()
        self._confirm_timer.start(max(0, int(ms)))

    def _finish_confirmation(self) -> None:
        self._confirming = False
        self.dismiss()

    def _on_close_clicked(self) -> None:
        self.close_clicked.emit()
        self.dismiss()
