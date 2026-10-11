"""HabitEditorSheet — create or edit a habit (spec §1.8).

440 px sheet: emoji button (popover with 24 curated emoji, or type one), name (required), cue,
"At a specific time" → TimeField, repeat DayChips + presets (Every day · Weekdays · Weekends),
reminder Toggle (disabled with "Reminders need a time" without a time), "Remind me even during
focus" (urgent), and "Count focus time" → minutes Stepper + project field (auto-complete habits).
New habits also offer the six suggestion chips; "Work deeply on your main project" pre-fills a
60-minute focus target.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QCompleter, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from madhura.core.days import ALL_DAYS, WEEKDAYS, WEEKENDS
from madhura.core.models import Habit
from madhura.ui import fonts, icons, theme
from madhura.ui.pages._habit_kit import CURATED_EMOJI, emoji_font, first_grapheme
from madhura.ui.shortcuts_sheet import display_keys
from madhura.ui.widgets.buttons import Button, LinkButton
from madhura.ui.widgets.chips import ChipGroup, DayChips
from madhura.ui.widgets.inputs import Stepper, TextField, TimeField
from madhura.ui.widgets.labels import Label, Micro
from madhura.ui.widgets.popover import Popover
from madhura.ui.widgets.settings_row import Divider
from madhura.ui.widgets.sheet import SheetContent
from madhura.ui.widgets.toggle import Toggle

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["HabitEditorSheet", "Suggestion", "SUGGESTIONS", "suggestion_for", "DEFAULT_TIME", "DEFAULT_FOCUS_MINUTES"]

log = logging.getLogger(__name__)

DEFAULT_TIME = "08:00"
DEFAULT_FOCUS_MINUTES = 60
NAME_MAX = 80
CUE_MAX = 80


@dataclass(frozen=True)
class Suggestion:
    """A starter habit (the three originals + three more)."""

    name: str
    emoji: str
    cue: str = ""
    time: str | None = None
    focus_minutes: int | None = None

    def as_preset(self) -> dict[str, Any]:
        """Editor pre-fill."""
        return {"name": self.name, "emoji": self.emoji, "cue": self.cue, "time": self.time, "focus_minutes": self.focus_minutes}


SUGGESTIONS: tuple[Suggestion, ...] = (
    Suggestion("Work deeply on your main project", "🎯", "Your first block of the day", None, DEFAULT_FOCUS_MINUTES),
    Suggestion("Ship something small", "🚀", "Before you wrap up"),
    Suggestion("Learn and apply one concept", "💡", "After lunch"),
    Suggestion("Read 10 pages", "📖", "Before bed"),
    Suggestion("Move your body", "🏃", "After work"),
    Suggestion("Plan tomorrow", "📝", "When you close your laptop"),
)


def suggestion_for(name: str) -> Suggestion | None:
    """The suggestion called *name* (case-insensitive), if any."""
    key = (name or "").strip().casefold()
    for s in SUGGESTIONS:
        if s.name.casefold() == key:
            return s
    return None


_EMOJI_NAMES = (
    "target", "book", "meditation", "running", "strength", "walking",
    "water", "salad", "apple", "sleep", "medicine", "brain",
    "light bulb", "writing", "memo", "laptop", "rocket", "guitar",
    "palette", "seedling", "broom", "phone", "gratitude", "sun",
)  # fmt: skip


def _vbox(spacing: int = 0) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


def _hbox(spacing: int = 0) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


# =========================================================================== emoji picker


class _EmojiButton(QAbstractButton):
    """A square button that shows an emoji (or a "smiley +" placeholder when empty)."""

    def __init__(self, size: int, emoji_px: int, parent: QWidget | None = None, *, bordered: bool = False) -> None:
        super().__init__(parent)
        self._emoji = ""
        self._px = emoji_px
        self._bordered = bordered
        self._hover = False
        self._kbd = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def emoji(self) -> str:
        """Current emoji ("" = none)."""
        return self._emoji

    def set_emoji(self, emoji: str | None) -> None:
        """Show *emoji* (None/"" → placeholder)."""
        self._emoji = emoji or ""
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def enterEvent(self, event: Any) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event: Any) -> None:  # noqa: N802
        self._kbd = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        self._kbd = False
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._bordered:
            p.setBrush(QColor(pal.input))
            border = pal.accent if (self.hasFocus() and self._kbd) else (pal.text_subtle if self._hover else pal.border_strong)
            p.setPen(QColor(border))
            p.drawRoundedRect(r, 8, 8)
        else:
            if self._hover or self.isDown() or (self.hasFocus() and self._kbd):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.hover))
                p.drawRoundedRect(r, 8, 8)
            if self.hasFocus() and self._kbd:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QColor(pal.accent))
                p.drawRoundedRect(r, 8, 8)
            if self.isChecked():
                p.setPen(QColor(pal.accent_border))
                p.setBrush(QColor(pal.accent_soft))
                p.drawRoundedRect(r, 8, 8)
        if self._emoji:
            p.setFont(emoji_font(self._px))
            p.setPen(QColor(pal.text))
            p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self._emoji)
        else:
            g = 20
            pm = icons.pixmap("sparkle", g, pal.text_subtle, self.devicePixelRatioF())
            p.drawPixmap((self.width() - g) // 2, (self.height() - g) // 2, pm)
        p.end()


class EmojiPicker(QWidget):
    """Popover content: a 6 × 4 grid of curated emoji, a "type your own" field and "Remove"."""

    picked = Signal(str)

    def __init__(self, current: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = _vbox(10)
        self.setLayout(v)
        title = Micro("Pick an emoji", self)
        v.addWidget(title)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.buttons: list[_EmojiButton] = []
        for i, (emo, name) in enumerate(zip(CURATED_EMOJI, _EMOJI_NAMES, strict=True)):
            b = _EmojiButton(36, 20, self)
            b.set_emoji(emo)
            b.setCheckable(True)
            b.setChecked(emo == current)
            b.setToolTip(name.capitalize())
            b.setAccessibleName(name)
            b.clicked.connect(lambda _c=False, e=emo: self.picked.emit(e))
            grid.addWidget(b, i // 6, i % 6)
            self.buttons.append(b)
        v.addLayout(grid)
        row = _hbox(8)
        self.field = TextField("Or type one", height=32, parent=self)
        self.field.set_max_length(16)
        self.field.returnPressed.connect(self._typed)
        self.field.setFixedWidth(150)
        row.addWidget(self.field)
        row.addStretch(1)
        self.remove = LinkButton("Remove", self, tone="muted", size_px=12)
        self.remove.setVisible(bool(current))
        self.remove.clicked.connect(lambda: self.picked.emit(""))
        row.addWidget(self.remove)
        v.addLayout(row)

    def _typed(self) -> None:
        emo = first_grapheme(self.field.text())
        if emo:
            self.picked.emit(emo)


# =========================================================================== form rows


class _FormRow(QWidget):
    """Title (+ optional description) on the left, controls on the right."""

    def __init__(self, title: str, description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = _hbox(12)
        self.setLayout(lay)
        texts = _vbox(2)
        self.title = Label(title, "form_title", self)
        self.title.setFont(fonts.font(14, 500))
        self.description = Label(description, "caption", self)
        self.description.setWordWrap(True)
        self.description.setVisible(bool(description))
        texts.addWidget(self.title)
        texts.addWidget(self.description)
        lay.addLayout(texts, 1)
        self.controls = _hbox(10)
        lay.addLayout(self.controls)
        self.setMinimumHeight(32)

    def add_control(self, widget: QWidget) -> QWidget:
        """Append a right-aligned control."""
        self.controls.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        if not widget.accessibleName():
            widget.setAccessibleName(self.title.text())
        return widget

    def set_description(self, text: str) -> None:
        """Change/clear the description."""
        self.description.setText(text)
        self.description.setVisible(bool(text))


# =========================================================================== sheet


class HabitEditorSheet(SheetContent):
    """New / edit habit sheet (440 px).

    ``HabitEditorSheet(ctx, habit_id=None, preset=None)``: *habit_id* (an id or a :class:`Habit`)
    edits that habit; *preset* (a dict of fields or a suggestion name) pre-fills a new one.
    ``habit=`` / ``prefill=`` are accepted as aliases. Emits ``saved(habit_id)`` after writing.
    """

    saved = Signal(int)
    sheet_width = 440

    def __init__(
        self,
        ctx: AppContext,
        habit_id: int | Habit | None = None,
        preset: dict[str, Any] | str | None = None,
        parent: QWidget | None = None,
        *,
        habit: int | Habit | None = None,
        prefill: dict[str, Any] | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        target = habit_id if habit_id is not None else habit
        if isinstance(target, Habit):
            self._habit: Habit | None = target
        elif target is not None:
            self._habit = ctx.repo.get_habit(int(target))
        else:
            self._habit = None
        self._emoji = ""
        self._reminder_pref = True
        self._popover: Popover | None = None
        self._saved_id: int | None = None
        editing = self._habit is not None
        self.set_title("Edit habit" if editing else "New habit")
        self.body.setSpacing(0)

        # --- suggestions (new habits only)
        self.suggestions: ChipGroup | None = None
        if not editing:
            self.body.addWidget(Micro("Start from a suggestion", self))
            self.body.addSpacing(8)
            self.suggestions = ChipGroup([(s.name, s.name) for s in SUGGESTIONS], True, None, self, size="sm", allow_empty=True, spacing=6)
            self.suggestions.setAccessibleName("Suggestions")
            self.suggestions.chipClicked.connect(self._on_suggestion)
            self.body.addWidget(self.suggestions)
            self.body.addSpacing(20)

        # --- identity
        ident = _hbox(8)
        self.emoji_button = _EmojiButton(40, 22, self, bordered=True)
        self.emoji_button.setToolTip("Choose an emoji")
        self.emoji_button.setAccessibleName("Emoji")
        self.emoji_button.clicked.connect(self.open_emoji_picker)
        ident.addWidget(self.emoji_button, 0, Qt.AlignmentFlag.AlignTop)
        self.name_field = TextField("Name — e.g. Read 10 pages", parent=self)
        self.name_field.set_max_length(NAME_MAX)
        self.name_field.line_edit.setAccessibleName("Habit name")
        self.name_field.returnPressed.connect(self.save)
        ident.addWidget(self.name_field, 1)
        self.body.addLayout(ident)
        self.body.addSpacing(10)
        self.cue_field = TextField("Cue (optional) — e.g. After morning coffee", icon="flag", parent=self)
        self.cue_field.set_max_length(CUE_MAX)
        self.cue_field.line_edit.setAccessibleName("Cue")
        self.cue_field.setToolTip("Stack it on something you already do")
        self.cue_field.returnPressed.connect(self.save)
        self.body.addWidget(self.cue_field)
        self.body.addSpacing(18)
        self.body.addWidget(Divider(self))
        self.body.addSpacing(14)

        # --- schedule
        self.repeat_row = _FormRow("Repeat", "", self)
        self.day_chips = DayChips(ALL_DAYS, self, diameter=28, gap=4)
        self.day_chips.maskChanged.connect(self._on_mask_changed)
        self.repeat_row.add_control(self.day_chips)
        self.body.addWidget(self.repeat_row)
        self.body.addSpacing(8)
        presets_row = _hbox(0)
        self.days_error = Label("Pick at least one day", "caption", self, tone="danger")
        self.days_error.hide()
        presets_row.addWidget(self.days_error, 0, Qt.AlignmentFlag.AlignVCenter)
        presets_row.addStretch(1)
        self.day_presets = ChipGroup(
            [(ALL_DAYS, "Every day"), (WEEKDAYS, "Weekdays"), (WEEKENDS, "Weekends")],
            True,
            ALL_DAYS,
            self,
            size="sm",
            allow_empty=True,
            wrap=False,
            spacing=4,
        )
        self.day_presets.setAccessibleName("Repeat presets")
        self.day_presets.chipClicked.connect(self._on_day_preset)
        presets_row.addWidget(self.day_presets)
        self.body.addLayout(presets_row)
        self.body.addSpacing(10)

        self.time_row = _FormRow("At a specific time", "", self)
        self.time_field = TimeField(DEFAULT_TIME, self, use_24h=ctx.use_24h(), height=32)
        self.time_field.setAccessibleName("Time")
        self.time_toggle = Toggle(False, self, accessible_name="At a specific time")
        self.time_row.add_control(self.time_field)
        self.time_row.add_control(self.time_toggle)
        self.time_toggle.toggled.connect(self._sync_enabled)
        self.body.addWidget(self.time_row)
        self.body.addSpacing(6)

        # --- reminders
        self.reminder_row = _FormRow("Remind me", "", self)
        self.reminder_toggle = Toggle(True, self, accessible_name="Remind me")
        self.reminder_toggle.clicked.connect(self._on_reminder_clicked)
        self.reminder_toggle.toggled.connect(lambda _on: self._sync_urgent())
        self.reminder_row.add_control(self.reminder_toggle)
        self.body.addWidget(self.reminder_row)
        self.body.addSpacing(6)
        self.urgent_row = _FormRow("Remind me even during focus", "", self)
        self.urgent_row.setToolTip("Otherwise the reminder waits until your focus block ends")
        self.urgent_toggle = Toggle(False, self, accessible_name="Remind me even during focus")
        self.urgent_row.add_control(self.urgent_toggle)
        self.body.addWidget(self.urgent_row)
        self.body.addSpacing(6)

        # --- focus habits
        self.focus_row = _FormRow("Count focus time", "", self)
        self.focus_row.setToolTip("Marked done automatically once today's focus time reaches the target")
        self.focus_toggle = Toggle(False, self, accessible_name="Count focus time")
        self.focus_toggle.toggled.connect(self._sync_focus)
        self.focus_row.add_control(self.focus_toggle)
        self.body.addWidget(self.focus_row)
        self.focus_box = QWidget(self)
        fbv = _vbox(0)
        self.focus_box.setLayout(fbv)
        fbv.addSpacing(8)
        fb = _hbox(10)
        fbv.addLayout(fb)
        self.minutes = Stepper(5, 240, 5, DEFAULT_FOCUS_MINUTES, " min", parent=self.focus_box)
        self.minutes.setFixedWidth(140)
        self.minutes.setAccessibleName("Focus minutes")
        fb.addWidget(self.minutes)
        self.project_field = TextField("Any project", icon=None, height=36, parent=self.focus_box)
        self.project_field.line_edit.setAccessibleName("Project")
        self.project_field.set_max_length(60)
        self.project_field.textChanged.connect(self._sync_project_dot)
        self.project_field.returnPressed.connect(self.save)
        try:
            projects = ctx.repo.projects()
        except Exception:  # noqa: BLE001 - suggestions are optional
            log.debug("Could not load project suggestions", exc_info=True)
            projects = []
        if projects:
            completer = QCompleter(projects, self.project_field)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.project_field.set_completer(completer)
        fb.addWidget(self.project_field, 1)
        self.focus_hint = Label("Done automatically once today's focus time reaches the target.", "caption", self.focus_box)
        self.focus_hint.setWordWrap(True)
        fbv.addSpacing(6)
        fbv.addWidget(self.focus_hint)
        self.body.addWidget(self.focus_box)

        # --- footer
        self.cancel_button = Button("Cancel", "ghost", parent=self)
        self.cancel_button.clicked.connect(self.close_sheet)
        self.save_button = Button("Save" if editing else "Create habit", "primary", parent=self)
        self.save_button.setToolTip(f"Save · {display_keys('Ctrl+Enter')}")
        self.save_button.clicked.connect(self.save)
        self.add_footer_button(self.cancel_button)
        self.add_footer_button(self.save_button, primary=True)

        self._load()
        if self._habit is None:
            self.apply_preset(preset if preset is not None else prefill)

    # ------------------------------------------------------------------ state
    @property
    def habit(self) -> Habit | None:
        """The habit being edited (None for a new one)."""
        return self._habit

    def is_editing(self) -> bool:
        """True when editing an existing habit."""
        return self._habit is not None

    def emoji(self) -> str:
        """The chosen emoji ("" = none)."""
        return self._emoji

    def set_emoji(self, emoji: str | None) -> None:
        """Set the emoji (first grapheme of *emoji*; ""/None clears)."""
        self._emoji = first_grapheme(emoji or "")
        self.emoji_button.set_emoji(self._emoji)
        self.emoji_button.setAccessibleDescription(self._emoji or "None")

    def _load(self) -> None:
        h = self._habit
        if h is not None:
            self.apply_preset(
                {
                    "name": h.name,
                    "emoji": h.emoji,
                    "cue": h.cue,
                    "time": h.time,
                    "days": h.days,
                    "reminder": h.reminder,
                    "urgent": h.urgent,
                    "focus_minutes": h.focus_minutes,
                    "project": h.project,
                }
            )
        else:
            self._sync_enabled()
            self._sync_focus()

    def apply_preset(self, preset: dict[str, Any] | str | None) -> None:
        """Fill the form from a dict of habit fields or a suggestion name."""
        if preset is None:
            return
        if isinstance(preset, str):
            s = suggestion_for(preset)
            data: dict[str, Any] = s.as_preset() if s is not None else {"name": preset}
        else:
            data = dict(preset)
        if "name" in data:
            self.name_field.set_text(str(data.get("name") or ""))
            self.name_field.set_error(None)
        if "emoji" in data:
            self.set_emoji(data.get("emoji"))
        if "cue" in data:
            self.cue_field.set_text(str(data.get("cue") or ""))
        if "time" in data:
            t = data.get("time")
            if t:
                self.time_field.set_time_str(str(t))
            self.time_toggle.setChecked(bool(t))
        if "days" in data and data.get("days") is not None:
            self.day_chips.set_mask(int(data["days"]))
            self._sync_day_presets()
        if "reminder" in data and data.get("reminder") is not None:
            self._reminder_pref = bool(data["reminder"])
        if "urgent" in data and data.get("urgent") is not None:
            self.urgent_toggle.setChecked(bool(data["urgent"]))
        if "focus_minutes" in data:
            fm = data.get("focus_minutes")
            if fm:
                self.minutes.set_value(int(fm))
            self.focus_toggle.setChecked(bool(fm))
        if "project" in data:
            self.project_field.set_text(str(data.get("project") or ""))
        self._sync_enabled()
        self._sync_focus()

    # ------------------------------------------------------------------ interactions
    def _on_suggestion(self, value: Any) -> None:
        s = suggestion_for(str(value))
        if self.suggestions is not None:
            self.suggestions.set_value(None)
        if s is None:
            return
        self.apply_preset(s.as_preset())
        self.name_field.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.name_field.line_edit.selectAll()

    def _on_mask_changed(self, _mask: int) -> None:
        self._sync_day_presets()
        if self.day_chips.mask():
            self.days_error.hide()

    def _on_day_preset(self, value: Any) -> None:
        self.day_chips.set_mask(int(value))
        self.day_presets.set_value(int(value))
        self.days_error.hide()

    def _sync_day_presets(self) -> None:
        mask = self.day_chips.mask()
        self.day_presets.set_value(mask if mask in (ALL_DAYS, WEEKDAYS, WEEKENDS) else None)

    def _on_reminder_clicked(self, checked: bool) -> None:
        self._reminder_pref = bool(checked)

    def _sync_enabled(self, *_args: Any) -> None:
        has_time = self.time_toggle.isChecked()
        self.time_field.setVisible(has_time)
        self.reminder_toggle.setEnabled(has_time)
        # dim the title like the (disabled) urgent row below; the "needs a time" hint stays readable
        self.reminder_row.title.setEnabled(has_time)
        self.reminder_toggle.setChecked(self._reminder_pref if has_time else False)
        self.reminder_row.set_description("" if has_time else "Reminders need a time")
        self._sync_urgent()
        self._relayout()

    def _sync_urgent(self) -> None:
        on = self.reminder_toggle.isEnabled() and self.reminder_toggle.isChecked()
        self.urgent_toggle.setEnabled(on)
        self.urgent_row.setEnabled(on)

    def _sync_focus(self, *_args: Any) -> None:
        self.focus_box.setVisible(self.focus_toggle.isChecked())
        self._sync_project_dot()
        self._relayout()

    def _sync_project_dot(self, *_args: Any) -> None:
        name = self.project_field.text().strip()
        self.project_field.set_leading_color(theme.project_color(name) if name else None)

    def _relayout(self) -> None:
        host = self.host()
        fn = getattr(host, "relayout", None) if host is not None else None
        if callable(fn):
            fn()

    def open_emoji_picker(self) -> None:
        """Show the emoji popover under the emoji button."""
        if self._popover is not None and self._popover.is_open():
            self._popover.dismiss()
            return
        picker = EmojiPicker(self._emoji)
        pop = Popover(self.emoji_button, picker, align="left")
        picker.picked.connect(self._on_picked)
        pop.closed.connect(lambda: setattr(self, "_popover", None))
        pop.closed.connect(pop.deleteLater)  # one popover per opening; don't pile them up on the window
        self._popover = pop
        pop.open()

    def emoji_popover(self) -> Popover | None:
        """The open emoji popover (tests)."""
        return self._popover

    def _on_picked(self, emoji: str) -> None:
        self.set_emoji(emoji)
        pop = self._popover
        if pop is not None:
            pop.dismiss()

    def initial_focus_widget(self) -> QWidget | None:
        return self.name_field.line_edit

    def on_shown(self) -> None:
        """Once the host has focused the name (its own zero-delay call, queued just before this
        one), a prefilled name is ready to retype or edit rather than leaving the cursor at its
        start: selected for a new habit (as when a suggestion chip is clicked), cursor at the end
        when editing."""
        QTimer.singleShot(0, self, self._place_name_cursor)

    def _place_name_cursor(self) -> None:
        edit = self.name_field.line_edit
        if not edit.hasFocus() or not edit.text() or edit.hasSelectedText():
            return
        if self.is_editing():
            edit.end(False)
        else:
            edit.selectAll()

    # ------------------------------------------------------------------ save
    def values(self) -> dict[str, Any]:
        """The habit fields as they would be saved."""
        has_time = self.time_toggle.isChecked()
        focus = self.focus_toggle.isChecked()
        project = self.project_field.text().strip()
        return {
            "name": " ".join(self.name_field.text().split()),
            "emoji": self._emoji or None,
            "cue": self.cue_field.text().strip() or None,
            "time": self.time_field.time_str() if has_time else None,
            "days": int(self.day_chips.mask()),
            "reminder": bool(self._reminder_pref),
            "urgent": bool(self.urgent_toggle.isChecked()),
            "focus_minutes": int(self.minutes.int_value()) if focus else None,
            "project": (project or None) if focus else None,
        }

    def validate(self) -> bool:
        """Show inline errors; True when the form can be saved."""
        data = self.values()
        ok = True
        if not data["name"]:
            self.name_field.set_error("Give your habit a name")
            self.name_field.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            ok = False
        if not data["days"]:
            self.days_error.show()
            ok = False
        self._relayout()
        return ok

    def save(self) -> int | None:
        """Validate, write, emit ``data_changed('habits')`` and close. Returns the habit id."""
        if not self.validate():
            return None
        ctx = self._ctx
        data = self.values()
        if self._habit is None:
            hid = ctx.repo.add_habit(**data)
        else:
            hid = self._habit.id
            ctx.repo.update_habit(hid, **data)
        self._saved_id = hid
        ctx.events.data_changed.emit("habits")
        if data["focus_minutes"] and ctx.habit_reminders is not None:
            ctx.habit_reminders.auto_complete()
        self.saved.emit(hid)
        self.close_sheet()
        return hid

    def saved_id(self) -> int | None:
        """Id written by the last successful save."""
        return self._saved_id
