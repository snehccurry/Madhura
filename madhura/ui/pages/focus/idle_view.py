"""Focus page — idle (planning) state (spec §1.2) [P1].

Building blocks, top to bottom::

    IdleView (PageScroll, max width 1040)
      _GoalHeader        greeting + long date │ 40px goal ring, "1h 20m / 2h", streak chip
      banners            firm nudge · carry-over · weekly recap (each hidden when irrelevant)
      columns            ≥ 900 px page width: [PlanCard, status line, UpNextCard] | 320 px
                         [TodayCard, ParkingLotCard]; narrower: one column in that order
      footer             "Wrap up the day" (WrapUpSheet)

Data is re-read when it changes (``events.data_changed`` / ``day_changed`` / settings) but only
while the view is on screen — otherwise the affected sections are marked dirty and refreshed when
it is shown again. Everything "today" uses ``ctx.today()`` (logical day).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCompleter,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from madhura.core.days import next_work_day
from madhura.core.models import PlannedTask, Session, to_date_str
from madhura.core.timefmt import (
    fmt_date_long,
    fmt_date_short,
    fmt_duration,
    fmt_preset,
    fmt_time,
    greeting,
    plan_heading,
)
from madhura.ui import fonts, icons, theme
from madhura.ui.pages.focus._idle_helpers import UndoQueue, goal_streak, session_caption, today_sessions
from madhura.ui.widgets.banner import InlineBanner
from madhura.ui.widgets.buttons import Button, IconButton, LinkButton
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.checkbox import Checkbox
from madhura.ui.widgets.chips import ChipGroup
from madhura.ui.widgets.inputs import Stepper, TextField
from madhura.ui.widgets.labels import Badge, Caption, ElidedLabel, Label, Micro
from madhura.ui.widgets.list_row import ListRow
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.page import PageScroll
from madhura.ui.widgets.popover import Popover
from madhura.ui.widgets.progress_ring import ProgressRing

try:
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover

    def _is_valid(_obj: object) -> bool:
        return True


if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "PlanCard",
    "UpNextCard",
    "TodayCard",
    "ParkingLotCard",
    "IdleView",
    "TASK_ERROR",
    "NUDGE_TEXT",
    "GOAL_TOAST_KEY",
    "CARRYOVER_KEY",
    "TWO_COLUMN_MIN_WIDTH",
]

log = logging.getLogger(__name__)

TASK_ERROR = "Name the one thing you'll work on"
NUDGE_TEXT = "You're between blocks. Plan the next one — or snooze."
GOAL_TOAST_KEY = "goal_toast_date"
CARRYOVER_KEY = "carryover_dismissed"
#: Page width (the scroll area, right of the sidebar) from which the idle view uses two columns.
TWO_COLUMN_MIN_WIDTH = 900
RIGHT_COLUMN_WIDTH = 320
LEFT_COLUMN_MIN_WIDTH = 480
CUSTOM = "custom"
DEFAULT_CUSTOM_MINUTES = 35
CUSTOM_MIN, CUSTOM_MAX, CUSTOM_STEP = 5, 240, 5
DEFAULT_TASK_MINUTES = 25
BREAK_LABELS = ("Break", "Lunch", "Meeting", "Admin")
BREAK_MINUTES = (15, 30, 45, 60)
BREAK_DEFAULT_MINUTES = {"Break": 15, "Lunch": 45, "Meeting": 30, "Admin": 30}

#: Section keys used for dirty tracking.
SECTIONS = ("header", "plan", "upnext", "today", "parking", "banners", "nudge")
_DATA_SECTIONS: dict[str, tuple[str, ...]] = {
    "sessions": ("header", "today", "plan"),
    "checkins": ("today",),
    "tasks": ("upnext", "banners", "plan"),
    "captures": ("parking",),
    "all": SECTIONS,
}
_SETTING_SECTIONS: dict[str, tuple[str, ...]] = {
    "duration_presets": ("plan", "upnext"),
    "default_minutes": ("plan",),
    "checkin_enabled": ("plan",),
    "checkin_interval_minutes": ("plan",),
    "time_format": ("plan", "today", "nudge"),
    "daily_goal_minutes": ("header",),
    "work_days": ("header",),
    "day_start_hour": SECTIONS,
    "nudge_mode": ("nudge",),
}


def _presets(ctx: AppContext) -> list[int]:
    try:
        values = sorted({int(v) for v in ctx.settings.get("duration_presets")})
    except (TypeError, ValueError):
        values = []
    return values or [15, 25, 45, 60, 90]


def _card_header(card: Card, title: str) -> tuple[QHBoxLayout, Label]:
    """A custom header row (title + extras) at the top of ``card.body``."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    label = Label(title, "h2", card)
    row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
    card.body.addLayout(row)
    return row, label


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()


def _same_widgets(a: list[Any], b: list[Any]) -> bool:
    """True when *a* and *b* hold the same widgets in the same order."""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


def _quote(text: str, limit: int = 40) -> str:
    text = text.strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"‘{text}’"


# =========================================================================== Plan card


class PlanCard(Card):
    """"Plan your next hour": task field, duration chips (+ Custom), project, Lock in.

    The heading follows the selected duration (``core.timefmt.plan_heading``); the caption shows a
    live "Ends at 14:35 · check-ins every 7 min". Enter in the task (or project) field, Lock in or
    Ctrl+Enter (via :meth:`start`) starts the session. "Not a focus block?" opens break chips.
    Emits ``started(session_id)`` after a successful start.
    """

    started = Signal(int)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(padding=24, parent=parent)
        self._ctx = ctx
        self._presets: list[int] = []
        self._custom_minutes: int | None = None
        self._planned_task_id: int | None = None
        self._prefill_title: str | None = None
        self._draft_title: str | None = None  # the user's own text (cursor at the end on focus)
        self._last_project: dict[str, str] = {}
        self._picked_title: str | None = None  # completer row highlighted (keyboard pick)
        self._arrow_nav = False
        self._one_row: bool | None = None
        self._custom_popover: Popover | None = None
        self._custom_stepper: Stepper | None = None
        self._break_popover: Popover | None = None
        self._break_labels: ChipGroup | None = None
        self._break_minutes: ChipGroup | None = None
        self._break_start: Button | None = None
        self._custom_key_filter: _EnterFilter | None = None
        self.body.setSpacing(12)

        self.heading = Label("", "h2", self)
        self.body.addWidget(self.heading)
        self.body.addSpacing(4)

        self.task_field = TextField("What will you focus on?", icon="target", height=52, parent=self)
        self.task_field.line_edit.setAccessibleName("Task")
        self.task_field.set_max_length(200)
        self.task_field.returnPressed.connect(self.start)
        self.task_field.textChanged.connect(self._on_task_text)
        self.task_field.textEdited.connect(self._on_task_edited)
        self.body.addWidget(self.task_field)

        self.duration = ChipGroup([], exclusive=True, parent=self, wrap=False)
        self.duration.setAccessibleName("Session length")
        self.duration.selectionChanged.connect(self._on_duration_changed)
        self.duration.chipClicked.connect(self._on_chip_clicked)
        self.duration.installEventFilter(self)

        self.project_field = TextField("Project", height=40, parent=self)
        self.project_field.setFixedWidth(200)
        self.project_field.line_edit.setAccessibleName("Project (optional)")
        self.project_field.set_max_length(60)
        self.project_field.textChanged.connect(lambda _t: self._sync_project_dot())
        self.project_field.returnPressed.connect(self.start)

        self.lock_in = Button("Lock in", "primary", icon="play", size="lg", parent=self)
        self.lock_in.setMinimumWidth(140)
        self.lock_in.setToolTip("Start focusing · Enter")
        self.lock_in.clicked.connect(self.start)

        # Row 2: [chips] (own row when narrow) · [project] ⟷ [Lock in]
        self._chips_row = QHBoxLayout()
        self._chips_row.setContentsMargins(0, 0, 0, 0)
        self._chips_row.setSpacing(12)
        self._action_row = QHBoxLayout()
        self._action_row.setContentsMargins(0, 0, 0, 0)
        self._action_row.setSpacing(12)
        self._action_row.addWidget(self.project_field, 0, Qt.AlignmentFlag.AlignVCenter)
        self._action_row.addStretch(1)
        self._action_row.addWidget(self.lock_in, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(self._chips_row)
        self.body.addLayout(self._action_row)

        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(12)
        self.helper = Caption("", self)
        self.helper.setFont(fonts.font(12, 400, tabular=True))
        row3.addWidget(self.helper, 1)
        self.not_focus_link = LinkButton("Not a focus block?", self, size_px=12)
        self.not_focus_link.setToolTip("Start a break, lunch, meeting or admin block instead")
        self.not_focus_link.clicked.connect(self.open_break_picker)
        row3.addWidget(self.not_focus_link, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(row3)

        self._task_model = QStringListModel(self)
        self._task_completer = self._make_completer(self._task_model)
        self.task_field.set_completer(self._task_completer)
        self._task_completer.activated[str].connect(self._on_title_completed)
        self._task_completer.highlighted[str].connect(self._on_title_highlighted)
        self._project_model = QStringListModel(self)
        self._project_completer = self._make_completer(self._project_model)
        self.project_field.set_completer(self._project_completer)

        self.rebuild_presets()
        self._place_controls(True)
        self._sync_project_dot()
        self.refresh()

    # ------------------------------------------------------------------ construction helpers

    def _on_task_text(self, text: str) -> None:
        # Completer insertions don't always emit textEdited; any real text clears the error.
        if text.strip() and self.task_field.has_error():
            self.task_field.set_error(None)

    def _on_task_edited(self, _text: str) -> None:
        self._picked_title = None  # typed past the highlighted completion

    def _make_completer(self, model: QStringListModel) -> QCompleter:
        c = QCompleter(model, self)
        c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        c.setFilterMode(Qt.MatchFlag.MatchContains)
        c.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        c.setMaxVisibleItems(7)
        return c

    def _place_controls(self, one_row: bool) -> None:
        if one_row == self._one_row:
            return
        self._one_row = one_row
        v = Qt.AlignmentFlag.AlignVCenter
        self._chips_row.removeWidget(self.duration)
        self._action_row.removeWidget(self.duration)
        while self._chips_row.count():
            self._chips_row.takeAt(0)
        if one_row:
            self._action_row.insertWidget(0, self.duration, 0, v)
        else:
            self._chips_row.addWidget(self.duration, 0, v)
            self._chips_row.addStretch(1)

    def _one_row_width(self) -> int:
        return self.duration.sizeHint().width() + 12 + 200 + 12 + max(140, self.lock_in.sizeHint().width())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # The row mode follows the width the parent gives us; never let the one-row grid's
        # minimum push the card wider (that would flip the mode back and forth).
        hint = super().minimumSizeHint()
        m = self._outer.contentsMargins()
        two_row = max(self.duration.sizeHint().width(), 200 + 12 + 140) + m.left() + m.right()
        return QSize(min(hint.width(), two_row), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_controls(self._fits_one_row())

    def is_single_row(self) -> bool:
        """True when chips, project and Lock in share one row (wide cards)."""
        return bool(self._one_row)

    # ------------------------------------------------------------------ duration

    def rebuild_presets(self) -> None:
        """Rebuild the duration chips from ``settings.duration_presets`` (keeps the selection)."""
        current = self.duration.value()
        current_minutes = self.minutes() if current is not None else int(self._ctx.settings.get("default_minutes"))
        self._presets = _presets(self._ctx)
        options: list[tuple[Any, str]] = [(m, fmt_preset(m)) for m in self._presets]
        options.append((CUSTOM, self._custom_label()))
        self.duration.set_options(options)
        self.set_minutes(current_minutes)
        self._place_controls(self._fits_one_row())

    def _fits_one_row(self) -> bool:
        m = self._outer.contentsMargins()
        width = self.width() - m.left() - m.right()
        return width >= self._one_row_width() if self.width() > 0 else True

    def _custom_label(self) -> str:
        return fmt_preset(self._custom_minutes) if self._custom_minutes else "Custom"

    def minutes(self) -> int:
        """Selected duration in minutes."""
        value = self.duration.value()
        if value == CUSTOM:
            return int(self._custom_minutes or DEFAULT_CUSTOM_MINUTES)
        if value is None:
            return int(self._ctx.settings.get("default_minutes"))
        return int(value)

    def set_minutes(self, minutes: int) -> None:
        """Select ``minutes`` (a preset chip, or Custom relabelled e.g. "35m")."""
        minutes = max(1, int(minutes))
        if minutes in self._presets:
            self.duration.set_value(minutes)
        else:
            self._custom_minutes = minutes
            self.duration.set_label(CUSTOM, self._custom_label())
            self.duration.set_value(CUSTOM)
        self.refresh()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.duration and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            nav = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End)
            if event.key() in nav:
                # Arrow keys select chips as they move; never pop the Custom stepper open then.
                self._arrow_nav = True
                QTimer.singleShot(0, self, self._clear_arrow_nav)
        return super().eventFilter(obj, event)

    def _clear_arrow_nav(self) -> None:
        self._arrow_nav = False

    def _on_chip_clicked(self, value: object) -> None:
        if value != CUSTOM:
            return
        if self._custom_minutes is None:
            self._custom_minutes = DEFAULT_CUSTOM_MINUTES
            self.duration.set_label(CUSTOM, self._custom_label())
        if not self._arrow_nav:
            QTimer.singleShot(0, self, self.open_custom_duration)

    def _on_duration_changed(self, _value: object) -> None:
        self.refresh()

    def open_custom_duration(self) -> None:
        """Open the Custom length popover (Stepper 5–240 min, step 5)."""
        if self._custom_popover is not None and self._custom_popover.is_open():
            return
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(Micro("Custom length", content))
        row = QHBoxLayout()
        row.setSpacing(8)
        stepper = Stepper(CUSTOM_MIN, CUSTOM_MAX, CUSTOM_STEP, self._custom_minutes or DEFAULT_CUSTOM_MINUTES, " min", parent=content)
        start = stepper.int_value()  # snapped to the 5-minute grid
        stepper.setAccessibleName("Custom length in minutes")
        stepper.valueChanged.connect(lambda v: self._apply_custom(int(round(v))))
        stepper.installEventFilter(self._custom_keys)
        row.addWidget(stepper)
        done = Button("Set", "primary", size="sm", parent=content)
        done.clicked.connect(self._close_custom)
        row.addWidget(done)
        lay.addLayout(row)
        anchor_rect = self.duration.chip_rect(CUSTOM)
        pop = Popover(self.duration, content, align="left", anchor_rect=anchor_rect if anchor_rect.isValid() else None)
        pop.closed.connect(self._on_custom_closed)
        self._custom_popover = pop
        self._custom_stepper = stepper
        self.duration.set_value(CUSTOM)
        self._apply_custom(start)
        pop.open()
        stepper.setFocus(Qt.FocusReason.PopupFocusReason)

    @property
    def _custom_keys(self) -> QObject:
        if self._custom_key_filter is None:
            self._custom_key_filter = _EnterFilter(self._close_custom, self)
        return self._custom_key_filter

    def _apply_custom(self, minutes: int) -> None:
        self._custom_minutes = max(CUSTOM_MIN, min(CUSTOM_MAX, int(minutes)))
        self.duration.set_label(CUSTOM, self._custom_label())
        if self.duration.value() != CUSTOM:
            self.duration.set_value(CUSTOM)
        self.refresh()

    def _close_custom(self) -> None:
        if self._custom_popover is not None:
            self._custom_popover.dismiss()

    def _on_custom_closed(self) -> None:
        pop = self._custom_popover
        self._custom_popover = None
        self._custom_stepper = None
        if self._custom_minutes in self._presets:
            # A custom value that equals a preset simply selects that preset.
            value = self._custom_minutes
            self._custom_minutes = None
            self.duration.set_label(CUSTOM, self._custom_label())
            self.duration.set_value(value)
            self.refresh()
        if pop is not None:
            pop.deleteLater()

    def custom_popover(self) -> Popover | None:
        """The open Custom-length popover (tests)."""
        return self._custom_popover

    def custom_stepper(self) -> Stepper | None:
        """The Stepper inside the open Custom-length popover (tests)."""
        return self._custom_stepper

    # ------------------------------------------------------------------ project

    def project(self) -> str | None:
        """Trimmed project text or None."""
        return self.project_field.text().strip() or None

    def _sync_project_dot(self) -> None:
        self.project_field.set_leading_color(theme.project_color(self.project_field.text()))

    def _on_title_highlighted(self, title: str) -> None:
        self._picked_title = title

    def _picked_project(self, title: str) -> str | None:
        """The remembered project when *title* is the completer row the user picked."""
        picked = self._picked_title
        if picked is None or picked.strip().casefold() != title.strip().casefold():
            return None
        return self._last_project.get(title.casefold())

    def _on_title_completed(self, title: str) -> None:
        self._picked_title = None
        if self._ctx.engine.is_active:
            return  # Return on the row already started the session (see start())
        if not self.project_field.text().strip():
            project = self._last_project.get(title.casefold())
            if project:
                self.project_field.set_text(project)

    def refresh_completers(self) -> None:
        """Reload recent task titles and project names for the completers."""
        repo = self._ctx.repo
        self._task_model.setStringList(repo.recent_task_titles(50))
        self._project_model.setStringList(repo.projects())
        last: dict[str, str] = {}
        for s in repo.recent_sessions(200, include_abandoned=False):
            if s.project:
                last.setdefault(s.task.casefold(), s.project)
        self._last_project = last

    # ------------------------------------------------------------------ preview + break picker

    def refresh(self) -> None:
        """Update the heading and the "Ends at … · check-ins …" caption."""
        minutes = self.minutes()
        self.heading.setText(plan_heading(minutes))
        s = self._ctx.settings
        ends = self._ctx.clock.now() + timedelta(minutes=minutes)
        if s.get("checkin_enabled"):
            checkins = f"check-ins every {int(s.get('checkin_interval_minutes'))} min"
        else:
            checkins = "check-ins off"
        self.helper.setText(f"Ends at {fmt_time(ends, self._ctx.use_24h())} · {checkins}")
        self.lock_in.setAccessibleDescription(f"Start a {minutes}-minute session")

    def open_break_picker(self) -> None:
        """"Not a focus block?" → Break / Lunch / Meeting / Admin × 15/30/45/60 min."""
        if self._break_popover is not None and self._break_popover.is_open():
            return
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(Micro("Not a focus block?", content))
        labels = ChipGroup(BREAK_LABELS, value="Break", parent=content, size="sm", wrap=False)
        labels.setAccessibleName("Block type")
        minutes = ChipGroup([(m, f"{m}m") for m in BREAK_MINUTES], value=BREAK_DEFAULT_MINUTES["Break"], parent=content, size="sm", wrap=False)
        minutes.setAccessibleName("Block length")
        lay.addWidget(labels)
        lay.addWidget(minutes)
        lay.addSpacing(2)
        start = Button("Start break", "primary", icon="coffee", size="sm", parent=content)
        start.clicked.connect(self.start_break_from_picker)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(start)
        lay.addLayout(row)
        labels.selectionChanged.connect(self._on_break_label)
        pop = Popover(self.not_focus_link, content, align="right")
        pop.closed.connect(self._on_break_closed)
        self._break_popover, self._break_labels, self._break_minutes, self._break_start = pop, labels, minutes, start
        pop.open()

    def _on_break_label(self, label: object) -> None:
        if self._break_minutes is None or self._break_start is None:
            return
        name = str(label or "Break")
        self._break_minutes.set_value(BREAK_DEFAULT_MINUTES.get(name, 30))
        self._break_start.setText(f"Start {name.lower()}")

    def break_picker(self) -> tuple[ChipGroup, ChipGroup, Button] | None:
        """(labels, minutes, start button) of the open break picker (tests)."""
        if self._break_popover is None or self._break_labels is None or self._break_minutes is None or self._break_start is None:
            return None
        return self._break_labels, self._break_minutes, self._break_start

    def start_break_from_picker(self) -> bool:
        """Start the block chosen in the break picker."""
        picker = self.break_picker()
        if picker is None:
            return False
        labels, minutes, _start = picker
        label = str(labels.value() or "Break")
        length = int(minutes.value() or BREAK_DEFAULT_MINUTES.get(label, 15))
        if self._break_popover is not None:
            self._break_popover.dismiss()
        return bool(self._ctx.engine.start_break(length, label))

    def _on_break_closed(self) -> None:
        pop = self._break_popover
        self._break_popover = self._break_labels = self._break_minutes = self._break_start = None
        if pop is not None:
            pop.deleteLater()

    # ------------------------------------------------------------------ prefill / start

    def prefill(self, task: PlannedTask | None) -> None:
        """Fill the card from an Up next item (title selected, its minutes) or clear it."""
        self.task_field.set_error(None)
        self._draft_title = None
        self._picked_title = None
        if task is None:
            self._planned_task_id = None
            self._prefill_title = None
            self.task_field.set_text("")
            self.project_field.set_text("")
            return
        self._planned_task_id = task.id
        self._prefill_title = task.title
        self.task_field.set_text(task.title, select=True)
        self.project_field.set_text(task.project or "")
        self.set_minutes(task.minutes)

    def set_draft(self, title: str, minutes: int) -> None:
        """Fill the card with text the user typed elsewhere (the break's "Next block").

        Not a prefill: Up next changes never replace it, and focusing the field puts the cursor
        at the end (typing continues the text). Starting links it to an Up next task with the
        same title, like any typed title; that task's project is shown.
        """
        title = title.strip()
        self.task_field.set_error(None)
        self._planned_task_id = None
        self._prefill_title = None
        self._draft_title = title
        self._picked_title = None
        self.task_field.set_text(title)
        task = self._linked_task(title) if title else None
        self.project_field.set_text((task.project or "") if task is not None else "")
        self.set_minutes(minutes)

    def sync_prefill(self, task: PlannedTask | None) -> None:
        """Keep an untouched prefill in step with Up next (the item was done, moved or deleted)."""
        if self._prefill_title is None or self.task_field.text() != self._prefill_title:
            return
        if task is not None and task.id == self._planned_task_id and task.title == self._prefill_title:
            return
        selected = self.task_field.line_edit.hasSelectedText()
        self.prefill(task)
        if task is not None and not selected:
            self.task_field.line_edit.deselect()

    def planned_task_id(self) -> int | None:
        """Up next task the current text is linked to (prefill), if any."""
        return self._planned_task_id

    def _linked_task(self, title: str) -> PlannedTask | None:
        repo = self._ctx.repo
        if self._planned_task_id is not None and title == (self._prefill_title or "").strip():
            task = repo.get_task(self._planned_task_id)
            if task is not None and not task.done:
                return task
        key = title.casefold()
        for task in repo.tasks_for_date(self._ctx.today(), include_done=False):
            if task.title.strip().casefold() == key:
                return task
        return None

    def start(self) -> int | None:
        """Start a session from the card. Returns the session id (None if not started)."""
        engine = self._ctx.engine
        if engine.is_active:
            return None
        title = self.task_field.text().strip()
        if not title:
            self.task_field.set_error(TASK_ERROR)
            self.task_field.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return None
        task = self._linked_task(title)
        project = self.project() or (task.project if task is not None else None)
        if project is None:
            # Return on a completer row starts *before* the completer's ``activated`` fills the
            # project (a mouse pick fills it first): apply the past task's project here too.
            project = self._picked_project(title)
        sid = engine.start(title, self.minutes(), project, planned_task_id=task.id if task is not None else None)
        if sid is None:
            return None
        self._planned_task_id = None
        self._prefill_title = None
        self._draft_title = None
        self._picked_title = None
        self.task_field.set_error(None)
        self.task_field.set_text("")
        self.started.emit(sid)
        return sid

    def focus_task(self) -> None:
        """Put keyboard focus in the task field: a prefill is selected (typing replaces it), the
        user's own draft (:meth:`set_draft`) gets the cursor at the end (typing continues it)."""
        edit = self.task_field.line_edit
        edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if self._draft_title is not None and edit.text().strip() == self._draft_title:
            edit.end(False)
        else:
            edit.selectAll()

    def close_popovers(self) -> None:
        """Dismiss any open popover (e.g. when the page switches state)."""
        for pop in (self._custom_popover, self._break_popover):
            if pop is not None:
                pop.dismiss()


class _EnterFilter(QObject):
    """Calls ``fn`` when Return/Enter is pressed in the watched widget."""

    def __init__(self, fn: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._fn()
                return True
        return False


# =========================================================================== Up next


class _TaskRow(ListRow):
    """One Up next row: round checkbox · title · minutes badge · ▶ · ⋯ (▶/⋯ on hover)."""

    edit_committed = Signal(int, str)
    edit_finished = Signal()

    def __init__(self, task: PlannedTask, parent: QWidget | None = None) -> None:
        super().__init__(40, parent, padding=(8, 8))
        self.task = task
        self._editor: TextField | None = None
        self._edit_done = False
        self.check = Checkbox(round=True, checked=task.done)
        self.check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_leading(self.check)
        self.title = self.set_title(task.title, "body")
        self.minutes_badge = Badge(fmt_preset(task.minutes), "neutral", self, clickable=True)
        self.minutes_badge.setToolTip("Change length")
        self.start_button = IconButton("play", "Start · Enter", 28, self, glyph=16)
        self.more_button = IconButton("more", "More", 28, self, glyph=16)
        for b in (self.start_button, self.more_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # ▶ / ⋯ appear on hover just left of the minutes badge, so the badges stay in one
        # right-aligned column with the add row's badge.
        self.add_trailing(self.start_button, hover_only=True)
        self.add_trailing(self.more_button, hover_only=True)
        self.trailing_layout.addSpacing(2)
        self.add_trailing(self.minutes_badge)
        self._keyboard_cursor = False
        self.set_task(task)

    def set_task(self, task: PlannedTask) -> None:
        """Show *task* (same id): the card updates rows in place instead of rebuilding them."""
        self.task = task
        name = _quote(task.title)
        if self.check.isChecked() != bool(task.done):
            self.check.setChecked(bool(task.done))
        self.check.setAccessibleName(f"Mark {name} done")
        if self.title.full_text() != task.title:
            self.title.set_text(task.title)
        if bool(self.title.property("tone")) != bool(task.done):  # re-polish only on change
            self.title.set_tone("muted" if task.done else None)
        self.title.setAccessibleName(task.title)
        self.setToolTip(task.title if not task.project else f"{task.title}\n{task.project}")
        if self.minutes_badge.text() != fmt_preset(task.minutes):
            self.minutes_badge.set_text(fmt_preset(task.minutes))
        self.minutes_badge.setAccessibleName(f"{task.minutes} minutes — change length")
        self.start_button.setAccessibleName(f"Start {name}")
        self.start_button.setEnabled(not task.done)
        self.more_button.setAccessibleName(f"More actions for {name}")

    def set_keyboard_cursor(self, on: bool) -> None:
        """Outline the row as the list's keyboard cursor."""
        if on != self._keyboard_cursor:
            self._keyboard_cursor = on
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._keyboard_cursor and self.is_selected():
            pal = theme.palette()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(theme.with_alpha(pal.accent, 0.55))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
            p.end()

    # ------------------------------------------------------------------ inline edit

    def is_editing(self) -> bool:
        """True while the inline title editor is open."""
        return self._editor is not None

    def editor(self) -> TextField | None:
        """The inline editor (while editing)."""
        return self._editor

    def begin_edit(self) -> None:
        """Replace the title with an inline text field (Enter saves, Esc cancels)."""
        if self._editor is not None:
            return
        self._edit_done = False
        editor = TextField(height=30, parent=self, text=self.task.title, font_size=14)
        editor.line_edit.setAccessibleName("Task title")
        editor.set_max_length(200)
        editor.editingFinished.connect(self._commit_edit)
        editor.line_edit.installEventFilter(self)
        self.title.hide()
        self.content_layout.insertWidget(0, editor)
        self._editor = editor
        self.set_actions_pinned(False)
        editor.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.line_edit.selectAll()

    def _commit_edit(self) -> None:
        if self._editor is None or self._edit_done:
            return
        text = self._editor.text().strip()
        self._end_edit()
        if text and text != self.task.title:
            self.edit_committed.emit(self.task.id, text)
        self.edit_finished.emit()

    def cancel_edit(self) -> None:
        """Close the editor without saving."""
        if self._editor is None:
            return
        self._end_edit()
        self.edit_finished.emit()

    def _end_edit(self) -> None:
        self._edit_done = True
        editor = self._editor
        self._editor = None
        if editor is not None:
            editor.line_edit.removeEventFilter(self)
            editor.hide()
            editor.deleteLater()
        self.title.show()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            self._editor is not None
            and obj is self._editor.line_edit
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            # Consume the keys here: QLineEdit ignores Return after handling it, and the row /
            # list would otherwise act on it too (re-open the editor, start the task).
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_edit()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._commit_edit()
                return True
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                return True  # no list navigation / reordering while editing
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Rows never take focus (the list does); don't turn keys bubbling up from children
        # into row clicks/activations.
        event.ignore()


class _TaskList(QWidget):
    """Keyboard-driven list of :class:`_TaskRow` (one tab stop).

    ↑/↓ select · Space done · Enter start · Delete delete · Alt+↑/↓ reorder · F2 edit ·
    Menu/Shift+F10 actions. ↓ on the last row moves to the add field.
    """

    def __init__(self, card: UpNextCard) -> None:
        super().__init__(card)
        self._card = card
        self.rows: list[_TaskRow] = []
        self._selected_id: int | None = None
        self._keyboard = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Up next tasks")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

    # ------------------------------------------------------------------ rows

    def sync_rows(self, tasks: list[PlannedTask], make_row: Callable[[PlannedTask], _TaskRow]) -> None:
        """Show *tasks* in order. Rows of known ids are updated in place, new ids get a row from
        *make_row*, rows of vanished ids are deleted, and the layout is only rebuilt when the order
        changed (a done toggle no longer re-creates every row). Keeps the selection by id."""
        known = {row.task.id: row for row in self.rows}
        rows: list[_TaskRow] = []
        for task in tasks:
            row = known.pop(task.id, None)
            if row is None:
                row = make_row(task)
            else:
                row.set_task(task)
            rows.append(row)
        for row in known.values():
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        if not _same_widgets(rows, self.rows):
            while self._layout.count():
                self._layout.takeAt(0)
            for row in rows:
                if row.parentWidget() is not self:
                    row.setParent(self)
                self._layout.addWidget(row)
                row.show()
        self.rows = rows
        ids = [r.task.id for r in rows]
        if self._selected_id not in ids:
            self._selected_id = None
        self._sync_selection()
        self.setVisible(bool(rows))

    def selected_id(self) -> int | None:
        """Task id of the keyboard selection."""
        return self._selected_id

    def selected_index(self) -> int:
        """Index of the selected row (-1 when none)."""
        for i, row in enumerate(self.rows):
            if row.task.id == self._selected_id:
                return i
        return -1

    def select_index(self, index: int) -> None:
        """Select row ``index`` (clamped)."""
        if not self.rows:
            self._selected_id = None
        else:
            index = max(0, min(len(self.rows) - 1, index))
            self._selected_id = self.rows[index].task.id
        self._sync_selection()

    def select_id(self, task_id: int | None) -> None:
        """Select the row of ``task_id``."""
        self._selected_id = task_id
        self._sync_selection()

    def _sync_selection(self) -> None:
        show = self.hasFocus()
        selected = -1
        for i, row in enumerate(self.rows):
            on = show and row.task.id == self._selected_id
            row.set_selected(on)
            row.set_keyboard_cursor(on and self._keyboard)
            if on:
                selected = i
        # Focus stays on the list itself: describe the selected row so assistive tech announces
        # each move (setAccessibleDescription sends QAccessible::DescriptionChanged).
        self.setAccessibleDescription(self.selection_summary(selected))

    def selection_summary(self, index: int) -> str:
        """"Reply to reviewer, 25 min, 2 of 4" for row *index* ("" when none)."""
        if not 0 <= index < len(self.rows):
            return ""
        task = self.rows[index].task
        done = ", done" if task.done else ""
        return f"{task.title}, {task.minutes} min{done}, {index + 1} of {len(self.rows)}"

    # ------------------------------------------------------------------ focus + keys

    def focusInEvent(self, event: Any) -> None:  # noqa: N802
        self._keyboard = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
            Qt.FocusReason.OtherFocusReason,
        )
        if self._selected_id is None and self.rows:
            back = event.reason() == Qt.FocusReason.BacktabFocusReason
            self._selected_id = self.rows[-1 if back else 0].task.id
        super().focusInEvent(event)
        self._sync_selection()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._sync_selection()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._keyboard:
            self._keyboard = True
            self._sync_selection()
        key = event.key()
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        idx = self.selected_index()
        card = self._card
        task_id = self._selected_id
        alt = mods == Qt.KeyboardModifier.AltModifier
        plain = mods == Qt.KeyboardModifier.NoModifier
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and alt and task_id is not None:
            card.move_task(task_id, -1 if key == Qt.Key.Key_Up else 1)
        elif key == Qt.Key.Key_Up and plain:
            self.select_index(idx - 1 if idx > 0 else 0)
        elif key == Qt.Key.Key_Down and plain:
            if idx >= len(self.rows) - 1:
                card.focus_add_field()
            else:
                self.select_index(idx + 1)
        elif key == Qt.Key.Key_Home and plain:
            self.select_index(0)
        elif key == Qt.Key.Key_End and plain:
            self.select_index(len(self.rows) - 1)
        elif key == Qt.Key.Key_Space and plain and task_id is not None:
            card.toggle_done(task_id, keep_index=True)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain and task_id is not None:
            card.start_task(task_id)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and plain and task_id is not None:
            if not event.isAutoRepeat():  # a held key deletes one task, not one per repeat
                card.delete_task(task_id)
        elif key == Qt.Key.Key_F2 and plain and task_id is not None:
            card.begin_edit(task_id)
        elif (key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and mods == Qt.KeyboardModifier.ShiftModifier)) and task_id is not None:
            card.open_menu(task_id)
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class UpNextCard(Card):
    """Today's planned tasks ("Up next"): rows, a permanent add row and keyboard control.

    Deleting is undoable (the row disappears now; the database delete happens when the "Undo"
    toast expires or on quit). Starting a row calls ``engine.start(title, minutes, project,
    planned_task_id=id)``.
    """

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, *, undo: UndoQueue | None = None) -> None:
        super().__init__(padding=(20, 18, 20, 16), parent=parent)
        self._ctx = ctx
        self._undo = undo or UndoQueue(ctx, self)
        self._pending_delete: set[int] = set()
        self._tasks: list[PlannedTask] = []
        self._add_minutes = DEFAULT_TASK_MINUTES
        self._menu: Menu | None = None
        self._editing = False
        self._deferred_refresh = False
        self.body.setSpacing(10)

        header, self._title = _card_header(self, "Up next")
        self.count_label = Label("", "h2", self, tone="subtle")
        self.count_label.setFont(fonts.font(16, 600, tabular=True))
        header.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.add_button = Button("Add", "ghost", icon="plus", size="sm", parent=self)
        self.add_button.setToolTip("Add a task")
        self.add_button.clicked.connect(self.focus_add_field)
        header.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.list = _TaskList(self)
        self.body.addWidget(self.list)
        self.empty_label = Caption("Nothing planned yet — add the next few things you'll focus on.", self)
        self.empty_label.setWordWrap(True)
        self.body.addWidget(self.empty_label)

        self.add_field = TextField("Add a task…", icon="plus", height=36, parent=self, font_size=13)
        self.add_field.line_edit.setAccessibleName("Add a task")
        self.add_field.set_max_length(200)
        self.add_minutes_badge = Badge(fmt_preset(self._add_minutes), "neutral", clickable=True)
        self.add_minutes_badge.setToolTip("Task length — click to change")
        self.add_minutes_badge.clicked.connect(self.cycle_add_minutes)
        self.add_field.set_trailing_widget(self.add_minutes_badge)
        self.add_field.returnPressed.connect(self._on_add_return)
        self.add_field.line_edit.installEventFilter(self)
        self.body.addWidget(self.add_field)
        self._sync_add_badge()

    # ------------------------------------------------------------------ data

    def tasks(self) -> list[PlannedTask]:
        """Tasks shown (unfinished by position, then done), excluding pending deletes."""
        return list(self._tasks)

    def unfinished(self) -> list[PlannedTask]:
        """Shown tasks that are not done."""
        return [t for t in self._tasks if not t.done]

    def first_unfinished(self) -> PlannedTask | None:
        """First unfinished task for today (the prefill candidate)."""
        rows = self.unfinished()
        return rows[0] if rows else None

    def row_for(self, task_id: int) -> _TaskRow | None:
        """The row widget of ``task_id``."""
        for row in self.list.rows:
            if row.task.id == task_id:
                return row
        return None

    def pending_deletes(self) -> set[int]:
        """Ids hidden while their delete can still be undone."""
        return set(self._pending_delete)

    def refresh(self) -> None:
        """Re-read today's tasks and rebuild the rows (deferred while a row is being edited)."""
        if self._editing:
            self._deferred_refresh = True
            return
        self._deferred_refresh = False
        today = self._ctx.today()
        all_tasks = [t for t in self._ctx.repo.tasks_for_date(today) if t.id not in self._pending_delete]
        undone = [t for t in all_tasks if not t.done]
        done = sorted((t for t in all_tasks if t.done), key=lambda t: (t.completed_at or t.created_at, t.id))
        self._tasks = undone + done
        self.list.sync_rows(self._tasks, self._make_row)
        n = len(undone)
        self.count_label.setText(str(n) if n else "")
        self.count_label.setVisible(n > 0)
        self.empty_label.setVisible(not self._tasks)
        self._sync_add_badge()

    def _make_row(self, task: PlannedTask) -> _TaskRow:
        row = _TaskRow(task, self.list)
        tid = task.id
        row.check.clicked.connect(lambda _c=False, t=tid: self.toggle_done(t))
        row.start_button.clicked.connect(lambda _c=False, t=tid: self.start_task(t))
        row.more_button.clicked.connect(lambda _c=False, t=tid: self.open_menu(t))
        row.minutes_badge.clicked.connect(lambda t=tid: self.cycle_task_minutes(t))
        row.clicked.connect(lambda t=tid: self._on_row_clicked(t))
        row.activated.connect(lambda t=tid: self.begin_edit(t))
        row.edit_committed.connect(self._on_edit_committed)
        row.edit_finished.connect(self._on_edit_finished)
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(lambda pos, t=tid: self.open_menu(t, pos))
        return row

    def _changed(self) -> None:
        self._ctx.events.data_changed.emit("tasks")

    def _task(self, task_id: int) -> PlannedTask | None:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return self._ctx.repo.get_task(task_id)

    # ------------------------------------------------------------------ actions

    def _on_row_clicked(self, task_id: int) -> None:
        self.list.select_id(task_id)
        self.list.setFocus(Qt.FocusReason.MouseFocusReason)
        self.list.select_id(task_id)

    def add_task(self, title: str, minutes: int | None = None) -> int | None:
        """Append a task for today. Returns its id (None for blank titles)."""
        title = (title or "").strip()
        if not title:
            return None
        tid = self._ctx.repo.add_task(title, int(minutes or self._add_minutes), plan_date=self._ctx.today())
        self._changed()
        return tid

    def _on_add_return(self) -> None:
        if self.add_task(self.add_field.text()) is not None:
            self.add_field.set_text("")
            self.add_field.line_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def focus_add_field(self) -> None:
        """Put keyboard focus in the add row."""
        self.add_field.line_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _next_preset(self, minutes: int) -> int:
        presets = _presets(self._ctx)
        for m in presets:
            if m > minutes:
                return m
        return presets[0]

    def cycle_add_minutes(self) -> int:
        """Advance the add row's minutes badge to the next preset (wraps)."""
        self._add_minutes = self._next_preset(self._add_minutes)
        self._sync_add_badge()
        return self._add_minutes

    def add_minutes(self) -> int:
        """Minutes new tasks get."""
        return self._add_minutes

    def _sync_add_badge(self) -> None:
        self.add_minutes_badge.set_text(fmt_preset(self._add_minutes))
        self.add_minutes_badge.setAccessibleName(f"New task length: {self._add_minutes} minutes")

    def cycle_task_minutes(self, task_id: int) -> None:
        """Advance a task's minutes to the next preset."""
        task = self._task(task_id)
        if task is None:
            return
        self._ctx.repo.update_task(task_id, minutes=self._next_preset(task.minutes))
        self._changed()

    def toggle_done(self, task_id: int, keep_index: bool = False) -> None:
        """Flip a task's done state."""
        task = self._task(task_id)
        if task is None:
            return
        index = self.list.selected_index()
        self._ctx.repo.set_task_done(task_id, not task.done)
        self._changed()
        if keep_index and index >= 0:
            self.list.select_index(index)

    def start_task(self, task_id: int) -> int | None:
        """Start a session for the task (ignored while a session runs or for done tasks)."""
        task = self._task(task_id)
        engine = self._ctx.engine
        if task is None or task.done or engine.is_active:
            return None
        return engine.start(task.title, task.minutes, task.project, planned_task_id=task.id)

    def delete_task(self, task_id: int) -> None:
        """Hide the task now; delete it when the "Undo" toast goes."""
        task = self._task(task_id)
        if task is None or task_id in self._pending_delete:
            return
        index = self.list.selected_index()
        had_focus = self.list.hasFocus()
        self._pending_delete.add(task_id)
        self.refresh()
        if had_focus:
            if self.list.rows:
                self.list.select_index(index)
            else:
                self.focus_add_field()

        def commit() -> None:
            self._pending_delete.discard(task_id)
            self._ctx.repo.delete_task(task_id)
            self._changed()

        def undo() -> None:
            self._pending_delete.discard(task_id)
            self.refresh()
            self.list.select_id(task_id)

        self._undo.push(f"Deleted {_quote(task.title, 32)}", commit, undo)

    def move_to_tomorrow(self, task_id: int) -> date | None:
        """Move the task to the next work day (undoable). Returns the new date."""
        task = self._task(task_id)
        if task is None:
            return None
        today = self._ctx.today()
        order = [t.id for t in self._ctx.repo.tasks_for_date(today)]
        target = next_work_day(today, int(self._ctx.settings.get("work_days")))
        self._ctx.repo.move_tasks_to_date([task_id], target)
        self._changed()
        when = "tomorrow" if target == today + timedelta(days=1) else fmt_date_short(target)

        def undo() -> None:
            repo = self._ctx.repo
            repo.move_tasks_to_date([task_id], today)
            present = {t.id for t in repo.tasks_for_date(today)}
            repo.reorder_tasks([i for i in order if i in present] + [i for i in present if i not in order])
            self._changed()

        self._undo.push(f"Moved to {when}", lambda: None, undo)
        return target

    def move_task(self, task_id: int, delta: int) -> bool:
        """Move an unfinished task up (-1) or down (+1) among unfinished tasks."""
        undone = [t.id for t in self.unfinished()]
        if task_id not in undone:
            return False
        i = undone.index(task_id)
        j = i + delta
        if not 0 <= j < len(undone):
            return False
        undone[i], undone[j] = undone[j], undone[i]
        done = [t.id for t in self._tasks if t.done]
        self._ctx.repo.reorder_tasks(undone + done + sorted(self._pending_delete))
        self._changed()
        self.list.select_id(task_id)
        return True

    def begin_edit(self, task_id: int) -> None:
        """Open the inline title editor of a row."""
        row = self.row_for(task_id)
        if row is None:
            return
        self._editing = True
        self.list.select_id(task_id)
        row.begin_edit()

    def _on_edit_committed(self, task_id: int, text: str) -> None:
        self._ctx.repo.update_task(task_id, title=text)
        self._deferred_refresh = True

    def _on_edit_finished(self) -> None:
        self._editing = False
        selected = self.list.selected_id()
        if self._deferred_refresh:
            QTimer.singleShot(0, self, self._after_edit)
        if selected is not None:
            self.list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _after_edit(self) -> None:
        if self._deferred_refresh:
            self._changed()
            self.refresh()

    def open_menu(self, task_id: int, pos: QPoint | None = None) -> Menu | None:
        """Show the ⋯ menu (Edit · Move to tomorrow · Delete) for a task."""
        row = self.row_for(task_id)
        task = self._task(task_id)
        if row is None or task is None:
            return None
        menu = Menu(self, delete_on_hide=True)
        menu.add_action("Edit", lambda: self.begin_edit(task_id), icon="edit", shortcut_text="F2")
        if not task.done:
            menu.add_action("Move to tomorrow", lambda: self.move_to_tomorrow(task_id), icon="calendar")
        menu.add_separator()
        menu.add_action("Delete", lambda: self.delete_task(task_id), icon="trash", shortcut_text="Del", danger=True)
        row.set_actions_pinned(True)
        menu.aboutToHide.connect(lambda r=row: _is_valid(r) and r.set_actions_pinned(False))
        self._menu = menu
        if pos is not None:
            menu.popup(row.mapToGlobal(pos))
        else:
            menu.popup_below(row.more_button, align="right")
        return menu

    def last_menu(self) -> Menu | None:
        """The most recently opened row menu (tests)."""
        return self._menu

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.add_field.line_edit and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Up and not event.modifiers() and self.list.rows:
                popup = self.add_field.line_edit.completer()
                if popup is None or not popup.popup().isVisible():
                    self.list.select_index(len(self.list.rows) - 1)
                    self.list.setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
        return super().eventFilter(obj, event)

    def flush(self) -> None:
        """Commit pending deletes now."""
        self._undo.flush()


# =========================================================================== Today


class _SessionRow(QWidget):
    """44 px row: start time · 2 px project rail · title / caption."""

    def __init__(self, ctx: AppContext, session: Session, caption: str, time_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._time_width = time_width
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        start_text = fmt_time(session.started_at, ctx.use_24h())
        self.time_label = Label(start_text, "caption", self)
        self.time_label.setFont(fonts.font(12, 400, tabular=True))
        self.time_label.setFixedWidth(time_width)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.time_label.setContentsMargins(0, 7, 0, 0)
        lay.addWidget(self.time_label)
        lay.addSpacing(26)  # 10 gap + 2 px rail + 14 gap
        text = QVBoxLayout()
        text.setContentsMargins(0, 5, 0, 5)
        text.setSpacing(1)
        self.title = ElidedLabel(session.task, "label", self)
        self.caption = ElidedLabel(caption, "caption", self)
        text.addWidget(self.title)
        text.addWidget(self.caption)
        lay.addLayout(text, 1)
        end = session.ended_at
        span = f"{start_text}–{fmt_time(end, ctx.use_24h())}" if end else start_text
        tip = [session.task, f"{span}" + (f" · {session.project}" if session.project else ""), caption]
        if session.note:
            tip.append(f"Note: {session.note}")
        self.setToolTip("\n".join(tip))
        self.setAccessibleName(f"{start_text} {session.task}, {caption}")

    def rail_color(self) -> QColor:
        """Project colour of the rail (theme-aware)."""
        return QColor(theme.project_color(self.session.project))

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.rail_color())
        p.drawRoundedRect(QRectF(self._time_width + 10, 8, 2, self.height() - 16), 1, 1)
        p.end()


class TodayCard(Card):
    """Today's counted sessions (completed / ended early) with the day's total."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        # Top inset 24 like the Plan card beside it: the two headings share a baseline.
        super().__init__(padding=(20, 24, 20, 14), parent=parent)
        self._ctx = ctx
        self._sessions: list[Session] = []
        self.body.setSpacing(6)
        header, self._title = _card_header(self, "Today")
        header.addStretch(1)
        self.total_label = Label("", "label", self)
        self.total_label.setFont(fonts.font(13, 600, tabular=True))
        header.addWidget(self.total_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addSpacing(2)
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        self.body.addLayout(self._rows)
        self.empty_label = Caption("No sessions yet today.", self)
        self.body.addWidget(self.empty_label)
        self.refresh()

    def sessions(self) -> list[Session]:
        """Sessions listed (oldest first)."""
        return list(self._sessions)

    def rows(self) -> list[_SessionRow]:
        """Row widgets (tests)."""
        out: list[_SessionRow] = []
        for i in range(self._rows.count()):
            w = self._rows.itemAt(i).widget()
            if isinstance(w, _SessionRow):
                out.append(w)
        return out

    def total_seconds(self) -> int:
        """Focused seconds today."""
        return sum(int(s.focused_seconds) for s in self._sessions)

    def _time_width(self) -> int:
        if self._ctx.use_24h():
            return 44
        fm = QFontMetrics(fonts.font(12, 400, tabular=True))
        return max(44, fm.horizontalAdvance("12:59 PM") + 6)

    def refresh(self) -> None:
        """Re-read today's sessions."""
        self._sessions = today_sessions(self._ctx)
        _clear_layout(self._rows)
        width = self._time_width()
        for s in self._sessions:
            caption = session_caption(s, self._ctx.repo.checkin_counts(s.id))
            self._rows.addWidget(_SessionRow(self._ctx, s, caption, width, self))
        total = self.total_seconds()
        self.total_label.setText(fmt_duration(total) if self._sessions else "")
        self.total_label.setAccessibleName(f"{fmt_duration(total)} focused today")
        self.empty_label.setVisible(not self._sessions)


# =========================================================================== Parking lot


class _CaptureRow(ListRow):
    """Parking-lot row: round checkbox (clear) · text · "→ Up next" (on hover / keyboard focus).

    The action takes no space while hidden, so the text can use the full width at rest.
    """

    def __init__(self, cap: Any, parent: QWidget | None = None) -> None:
        super().__init__(40, parent, clickable=False, padding=(4, 4))
        self.capture = cap
        self.check = Checkbox(round=True)
        self.check.setToolTip("Clear")
        self.add_leading(self.check)
        self.title = self.set_title(cap.text, "body")
        self.to_next = LinkButton("Up next", self, icon="arrow_right", size_px=12)
        self.to_next.setToolTip("Add to today's Up next")
        self.add_trailing(self.to_next)
        self.set_capture(cap)
        self.to_next.hide()
        self.hoveredChanged.connect(lambda _h: self._sync_action())
        for w in (self.check, self.to_next):
            w.installEventFilter(self)

    def set_capture(self, cap: Any) -> None:
        """Show *cap* (same id) — the card updates rows in place instead of rebuilding them."""
        self.capture = cap
        name = _quote(cap.text)
        self.check.setAccessibleName(f"Clear {name}")
        if self.title.full_text() != cap.text:
            self.title.set_text(cap.text)
        self.setToolTip(cap.text)
        self.to_next.setAccessibleName(f"Move {name} to Up next")

    def _sync_action(self) -> None:
        fw = QApplication.focusWidget()
        focused = fw is not None and (fw is self or self.isAncestorOf(fw))
        self.to_next.setVisible(self.is_hovered() or focused)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self, self._sync_action)
        return super().eventFilter(obj, event)


class ParkingLotCard(Card):
    """Open captures (stray thoughts parked during sessions). Hidden when empty."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, *, undo: UndoQueue | None = None) -> None:
        super().__init__(padding=(20, 18, 20, 14), parent=parent)
        self._ctx = ctx
        self._undo = undo or UndoQueue(ctx, self)
        self._captures: list[Any] = []
        self.body.setSpacing(8)
        header, self._title = _card_header(self, "Parking lot")
        self.count_label = Label("", "h2", self, tone="subtle")
        self.count_label.setFont(fonts.font(16, 600, tabular=True))
        header.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)
        self.body.addLayout(self._rows)
        self.refresh()

    def captures(self) -> list[Any]:
        """Open captures shown."""
        return list(self._captures)

    def rows(self) -> list[_CaptureRow]:
        """Row widgets (tests)."""
        out: list[_CaptureRow] = []
        for i in range(self._rows.count()):
            w = self._rows.itemAt(i).widget()
            if isinstance(w, _CaptureRow):
                out.append(w)
        return out

    def refresh(self) -> None:
        """Re-read open captures (the card hides itself when there are none).

        Rows are kept per capture id (updated in place; created / deleted as captures come and
        go) and the layout is only rebuilt when the order changed — clearing one of 100 open
        captures no longer re-creates the other 99.
        """
        self._captures = self._ctx.repo.open_captures()
        old = self.rows()
        known = {row.capture.id: row for row in old}
        rows: list[_CaptureRow] = []
        for cap in self._captures:
            row = known.pop(cap.id, None)
            if row is None:
                row = self._make_row(cap)
            else:
                row.set_capture(cap)
            rows.append(row)
        for row in known.values():
            self._rows.removeWidget(row)
            row.hide()
            row.deleteLater()
        if not _same_widgets(rows, old):
            while self._rows.count():
                self._rows.takeAt(0)
            for row in rows:
                self._rows.addWidget(row)
        n = len(self._captures)
        self.count_label.setText(str(n))
        self.setVisible(n > 0)

    def _make_row(self, cap: Any) -> _CaptureRow:
        row = _CaptureRow(cap, self)
        row.check.clicked.connect(lambda _c=False, cid=cap.id: self.clear_capture(cid))
        row.to_next.clicked.connect(lambda _c=False, cid=cap.id: self.move_to_up_next(cid))
        return row

    def _capture(self, capture_id: int) -> Any:
        for c in self._captures:
            if c.id == capture_id:
                return c
        return None

    def clear_capture(self, capture_id: int) -> None:
        """Mark a capture cleared (undo toast)."""
        cap = self._capture(capture_id)
        if cap is None:
            return
        repo = self._ctx.repo
        repo.set_capture_done(capture_id, True)
        self._ctx.events.data_changed.emit("captures")

        def undo() -> None:
            repo.set_capture_done(capture_id, False)
            self._ctx.events.data_changed.emit("captures")

        self._undo.push(f"Cleared {_quote(cap.text, 32)}", lambda: None, undo)

    def move_to_up_next(self, capture_id: int) -> int | None:
        """Create today's Up next task from a capture and clear it (undo toast)."""
        cap = self._capture(capture_id)
        if cap is None:
            return None
        repo = self._ctx.repo
        with repo.transaction():
            tid = repo.add_task(cap.text, DEFAULT_TASK_MINUTES, plan_date=self._ctx.today())
            repo.set_capture_done(capture_id, True)
        self._ctx.events.data_changed.emit("tasks")
        self._ctx.events.data_changed.emit("captures")

        def undo() -> None:
            with repo.transaction():
                repo.delete_task(tid)
                repo.set_capture_done(capture_id, False)
            self._ctx.events.data_changed.emit("tasks")
            self._ctx.events.data_changed.emit("captures")

        self._undo.push("Moved to Up next", lambda: None, undo)
        return tid


# =========================================================================== header


class _StreakChip(QWidget):
    """24 px pill: flame + streak count (warm tone, never "danger")."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self.setFont(fonts.font(12, 600, tabular=True))
        self.setFixedHeight(24)

    def count(self) -> int:
        """Streak shown."""
        return self._count

    def text(self) -> str:
        """The number shown."""
        return str(self._count)

    def set_count(self, n: int) -> None:
        """Set the streak count."""
        self._count = int(n)
        self.setFixedWidth(8 + 14 + 4 + QFontMetrics(self.font()).horizontalAdvance(str(self._count)) + 10)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.warning_soft))
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        pm = icons.pixmap("flame", 14, pal.warning, self.devicePixelRatioF())
        p.drawPixmap(8, int((r.height() - 14) / 2), pm)
        p.setPen(QColor(pal.warning))
        p.setFont(self.font())
        p.drawText(QRectF(26, 0, r.width() - 26, r.height()), int(Qt.AlignmentFlag.AlignVCenter), str(self._count))
        p.end()


class _GoalHeader(QWidget):
    """48 px header: greeting h1 + long date │ goal ring, "1h 20m / 2h", streak chip."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._reached = False
        self.setMinimumHeight(48)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.greeting = Label("", "h1", self)
        self.date_label = Caption("", self)
        text.addWidget(self.greeting)
        text.addWidget(self.date_label)
        lay.addLayout(text, 1)
        self.goal_ring = ProgressRing(40, 4, self)
        self.goal_ring.setAccessibleName("Daily goal")
        lay.addWidget(self.goal_ring, 0, Qt.AlignmentFlag.AlignVCenter)
        goal_text = QHBoxLayout()
        goal_text.setContentsMargins(0, 0, 0, 0)
        goal_text.setSpacing(4)
        self.goal_total = Label("", "label", self)
        self.goal_total.setFont(fonts.font(13, 600, tabular=True))
        self.goal_target = Label("", "label", self, tone="muted")
        self.goal_target.setFont(fonts.font(13, 500, tabular=True))
        goal_text.addWidget(self.goal_total)
        goal_text.addWidget(self.goal_target)
        lay.addLayout(goal_text)
        lay.addSpacing(4)
        self.streak = _StreakChip(self)
        lay.addWidget(self.streak, 0, Qt.AlignmentFlag.AlignVCenter)
        self.streak.hide()

    def set_greeting(self) -> None:
        """Greeting + long date for now / the logical today."""
        self.greeting.setText(greeting(self._ctx.clock.now()))
        self.date_label.setText(fmt_date_long(self._ctx.today()))

    def set_goal(self, total_seconds: int, goal_seconds: int) -> bool:
        """Update ring + label. Returns True when the goal is reached."""
        fraction = (total_seconds / goal_seconds) if goal_seconds > 0 else 0.0
        self._reached = goal_seconds > 0 and total_seconds >= goal_seconds
        self.goal_ring.set_progress(min(1.0, fraction), animate=self.isVisible())
        self._apply_colors()
        self.goal_total.setText(fmt_duration(total_seconds))
        self.goal_target.setText(f"/ {fmt_duration(goal_seconds)}")
        desc = f"{fmt_duration(total_seconds)} of {fmt_duration(goal_seconds)} focused today"
        if self._reached:
            desc += " — goal reached"
        self.goal_ring.setAccessibleDescription(desc)
        for w in (self.goal_ring, self.goal_total, self.goal_target):
            w.setToolTip(f"Daily goal: {desc}")
        return self._reached

    def goal_text(self) -> str:
        """``"1h 20m / 2h"``."""
        return f"{self.goal_total.text()} {self.goal_target.text()}"

    def goal_reached(self) -> bool:
        """True when today's total reached the goal."""
        return self._reached

    def _apply_colors(self) -> None:
        pal = theme.palette()
        self.goal_ring.set_color(pal.success if self._reached else pal.accent)
        self.goal_total.set_tone("success" if self._reached else None)

    def set_streak(self, current: int, best: int) -> None:
        """Flame chip with the current streak (hidden at 0)."""
        self.streak.set_count(current)
        self.streak.setVisible(current > 0)
        days = "day" if current == 1 else "days"
        tip = f"{current}-day goal streak"
        if best > current:
            tip += f" · best {best}"
        self.streak.setToolTip(tip)
        self.streak.setAccessibleName(f"Goal streak: {current} {days}")

    def on_theme_changed(self) -> None:
        """Re-apply token colours after a theme / accent switch."""
        self._apply_colors()


# =========================================================================== nudge status line


class _NudgeStatus(QWidget):
    """"Nudges snoozed until 14:30 · Resume" / "Done for today · Resume nudges"."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 10, 0, 0)
        lay.setSpacing(6)
        self.icon_label = Label("", "caption", self)
        self.text_label = Caption("", self)
        self.resume = LinkButton("Resume", self, tone="accent", size_px=12, weight=600)
        self.resume.clicked.connect(self._resume)
        lay.addWidget(self.text_label)
        lay.addWidget(self.resume)
        lay.addStretch(1)
        self.icon_label.hide()
        self.hide()

    def refresh(self) -> None:
        """Show the line only while nudges are snoozed or done for today."""
        nudges = self._ctx.nudges
        if nudges is None or self._ctx.settings.get("nudge_mode") == "off":
            self.hide()
            return
        try:
            done = bool(nudges.is_done_for_today)
            until = nudges.snoozed_until
        except Exception:  # noqa: BLE001 - a broken service must not break the page
            log.exception("Could not read nudge state")
            self.hide()
            return
        if done:
            self.text_label.setText("Done for today ·")
            self.resume.setText("Resume nudges")
        elif until is not None:
            self.text_label.setText(f"Nudges snoozed until {fmt_time(until, self._ctx.use_24h())} ·")
            self.resume.setText("Resume")
        else:
            self.hide()
            return
        self.show()

    def _resume(self) -> None:
        nudges = self._ctx.nudges
        if nudges is not None:
            nudges.resume()
        self.refresh()


# =========================================================================== idle view


class IdleView(PageScroll):
    """The planning view (see module docstring). ``refresh()`` re-reads everything."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(1040, parent, spacing=0)
        self._ctx = ctx
        self._dirty: set[str] = set(SECTIONS)
        self._two_columns: bool | None = None
        self._nudge_visible = False
        self._recap_visible = False
        self._recap_dismissed = False
        self._nudges_hooked: Any = None
        self.undo = UndoQueue(ctx, self)
        col = self.column

        self.header = _GoalHeader(ctx, col)
        self.content_layout.addWidget(self.header)
        self.content_layout.addSpacing(24)

        # Banners (each hidden when irrelevant; the box hides when all are).
        self.banner_box = QWidget(col)
        bl = QVBoxLayout(self.banner_box)
        bl.setContentsMargins(0, 0, 0, 16)
        bl.setSpacing(12)
        self.nudge_banner = InlineBanner("accent", NUDGE_TEXT, icon="bell", parent=self.banner_box)
        self.nudge_banner.hide()
        self.carryover_banner = InlineBanner("info", "", icon="inbox", parent=self.banner_box)
        self.carryover_banner.set_actions([("Move to today", self.move_carryover_to_today), ("Dismiss", self.dismiss_carryover)])
        self.carryover_banner.hide()
        self.recap = self._make_recap()
        bl.addWidget(self.nudge_banner)
        bl.addWidget(self.carryover_banner)
        if self.recap is not None:
            bl.addWidget(self.recap)
            self.recap.hide()
        self.content_layout.addWidget(self.banner_box)

        # Columns.
        self.columns = QWidget(col)
        self._columns_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.columns)
        self._columns_layout.setContentsMargins(0, 0, 0, 0)
        self.left_column = QWidget(self.columns)
        left = QVBoxLayout(self.left_column)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        self.plan_card = PlanCard(ctx, self.left_column)
        self.nudge_status = _NudgeStatus(ctx, self.left_column)
        self.up_next = UpNextCard(ctx, self.left_column, undo=self.undo)
        left.addWidget(self.plan_card)
        left.addWidget(self.nudge_status)
        left.addSpacing(16)
        left.addWidget(self.up_next)
        left.addStretch(1)
        self.right_column = QWidget(self.columns)
        right = QVBoxLayout(self.right_column)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)
        self.today_card = TodayCard(ctx, self.right_column)
        self.parking_lot = ParkingLotCard(ctx, self.right_column, undo=self.undo)
        right.addWidget(self.today_card)
        right.addWidget(self.parking_lot)
        right.addStretch(1)
        self._columns_layout.addWidget(self.left_column, 1)
        self._columns_layout.addWidget(self.right_column, 0)
        # Never taller than the taller column: keeps the footer right under the content.
        self.columns.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.content_layout.addWidget(self.columns)

        self.content_layout.addSpacing(32)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.wrap_up_link = LinkButton("Wrap up the day", col, icon="sunset")
        self.wrap_up_link.setToolTip("Review today and set up tomorrow's first block")
        self.wrap_up_link.clicked.connect(self.open_wrap_up)
        footer.addWidget(self.wrap_up_link)
        footer.addStretch(1)
        self.content_layout.addLayout(footer)

        self._minute_timer = QTimer(self)
        self._minute_timer.setSingleShot(True)
        self._minute_timer.timeout.connect(self._on_minute)

        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.events.day_changed.connect(self._on_day_changed)
        ctx.settings.changed.connect(self._on_setting_changed)
        ctx.theme.changed.connect(self._on_theme_changed)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.undo.flush)

        self.set_two_columns(True)
        self.refresh()

    # ------------------------------------------------------------------ construction

    def _make_recap(self) -> QWidget | None:
        try:
            from madhura.ui.widgets.recap import WeeklyRecapCard

            recap = WeeklyRecapCard(self._ctx, self.banner_box)
        except Exception:  # noqa: BLE001 - the recap card is optional ([I] owns it)
            log.exception("Could not create the weekly recap card")
            return None
        dismissed = getattr(recap, "dismissed", None)
        if dismissed is not None:
            dismissed.connect(self._on_recap_dismissed)
        return recap

    # ------------------------------------------------------------------ layout

    def is_two_columns(self) -> bool:
        """True in the wide (two-column) layout."""
        return bool(self._two_columns)

    def set_two_columns(self, two: bool) -> None:
        """Switch between the two-column and the single-column layout."""
        if two == self._two_columns:
            return
        self._two_columns = two
        lay = self._columns_layout
        if two:
            lay.setDirection(QBoxLayout.Direction.LeftToRight)
            lay.setSpacing(20)
            self.right_column.setFixedWidth(RIGHT_COLUMN_WIDTH)
            self.left_column.setMinimumWidth(LEFT_COLUMN_MIN_WIDTH)
        else:
            lay.setDirection(QBoxLayout.Direction.TopToBottom)
            lay.setSpacing(16)
            self.right_column.setMinimumWidth(0)
            self.right_column.setMaximumWidth(16777215)
            self.left_column.setMinimumWidth(0)
        self._sync_column_spacing()

    def _sync_column_spacing(self) -> None:
        # In one column the right cards stack under Up next; drop the gap when they're all hidden.
        self.right_column.setVisible(self._two_columns or self.today_card.isVisibleTo(self.right_column) or self.parking_lot.isVisibleTo(self.right_column))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.set_two_columns(self.width() >= TWO_COLUMN_MIN_WIDTH)

    # ------------------------------------------------------------------ visibility / refresh

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._arm_minute_timer()
        if self._dirty:
            QTimer.singleShot(0, self, self.refresh_dirty)

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        super().hideEvent(event)
        self._minute_timer.stop()

    def on_shown(self) -> None:
        """Called when the Focus page is shown: refresh what changed while hidden."""
        self._hook_services()
        self.refresh_dirty()

    def mark_dirty(self, sections: Iterable[str]) -> None:
        """Mark sections stale; refresh them now when on screen."""
        self._dirty.update(sections)
        if self.isVisible():
            self.refresh_dirty()

    def refresh(self) -> None:
        """Re-read everything."""
        self._dirty.update(SECTIONS)
        self.refresh_dirty()

    def refresh_dirty(self) -> None:
        """Refresh the stale sections."""
        self._hook_services()
        dirty, self._dirty = self._dirty, set()
        if not dirty:
            return
        try:
            if "plan" in dirty:
                self.plan_card.rebuild_presets()
                self.plan_card.refresh_completers()
                self.plan_card.refresh()
            if "upnext" in dirty:
                self.up_next.refresh()
                self.plan_card.sync_prefill(self.up_next.first_unfinished())
            if "today" in dirty:
                self.today_card.refresh()
            if "parking" in dirty:
                self.parking_lot.refresh()
            if "header" in dirty:
                self._refresh_header()
            if "banners" in dirty:
                self._refresh_banners()
            if "nudge" in dirty:
                self.nudge_status.refresh()
                self._refresh_nudge_banner()
        except Exception:  # noqa: BLE001 - a refresh error must not take the page down
            log.exception("Focus page refresh failed")
        self._sync_column_spacing()

    def dirty_sections(self) -> set[str]:
        """Sections waiting for a refresh (tests)."""
        return set(self._dirty)

    def _on_data_changed(self, kind: str) -> None:
        sections = _DATA_SECTIONS.get(kind)
        if sections:
            self.mark_dirty(sections)

    def _on_day_changed(self) -> None:
        if self._recap_dismissed:
            # A new day may start a new ISO week (the app can stay in the tray for weeks): let
            # should_show() decide again — weekly_recap_seen keeps a dismissed week hidden.
            # (A visible card is left alone: re-asking would hide it, as it's marked seen.)
            self._recap_dismissed = False
            self._recap_visible = False
        self.mark_dirty(SECTIONS)

    def _on_setting_changed(self, key: str) -> None:
        sections = _SETTING_SECTIONS.get(key)
        if sections:
            self.mark_dirty(sections)

    def _on_theme_changed(self) -> None:
        self.header.on_theme_changed()
        self.plan_card._sync_project_dot()  # noqa: SLF001 - same module
        self.update()

    def _arm_minute_timer(self) -> None:
        now = self._ctx.clock.now()
        ms = int((60 - now.second) * 1000 - now.microsecond / 1000) + 50
        self._minute_timer.start(max(1000, min(61_000, ms)))

    def _on_minute(self) -> None:
        if not self.isVisible():
            return
        self.header.set_greeting()
        self.plan_card.refresh()
        self.nudge_status.refresh()
        self._arm_minute_timer()

    def _hook_services(self) -> None:
        # Feature services start after the main window is built; connect lazily (bound method,
        # so the connection dies with this view).
        nudges = self._ctx.nudges
        if nudges is not None and nudges is not self._nudges_hooked:
            signal = getattr(nudges, "state_changed", None)
            if signal is not None:
                signal.connect(self._on_nudges_changed)
            self._nudges_hooked = nudges

    def _on_nudges_changed(self, *_args: object) -> None:
        self.mark_dirty(("nudge",))

    # ------------------------------------------------------------------ header + goal

    def _refresh_header(self) -> None:
        ctx = self._ctx
        self.header.set_greeting()
        total = sum(int(s.focused_seconds) for s in today_sessions(ctx))
        goal = int(ctx.settings.get("daily_goal_minutes")) * 60
        reached = self.header.set_goal(total, goal)
        try:
            current, best = goal_streak(ctx)
        except Exception:  # noqa: BLE001 - streak is decoration; never break the header
            log.exception("Could not compute the goal streak")
            current, best = 0, 0
        self.header.set_streak(current, best)
        if reached:
            self._maybe_goal_toast(goal)

    def _maybe_goal_toast(self, goal_seconds: int) -> None:
        window = self._ctx.window
        if window is None or not self.isVisible():
            return
        today = to_date_str(self._ctx.today())
        repo = self._ctx.repo
        if repo.kv_get(GOAL_TOAST_KEY) == today:
            return
        repo.kv_set(GOAL_TOAST_KEY, today)
        window.show_toast(f"Daily goal reached — {fmt_duration(goal_seconds)} focused", "success")

    # ------------------------------------------------------------------ banners

    def _refresh_banners(self) -> None:
        ctx = self._ctx
        today = ctx.today()
        carry = ctx.repo.unfinished_before(today)
        dismissed = ctx.repo.kv_get(CARRYOVER_KEY) == to_date_str(today)
        if carry and not dismissed:
            self.carryover_banner.set_text(f"{len(carry)} unfinished from earlier")
            self.carryover_banner.show()
        else:
            self.carryover_banner.hide()
        if self.recap is not None:
            if not self._recap_dismissed and not self._recap_visible:
                refresh = getattr(self.recap, "refresh", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:  # noqa: BLE001 - optional card
                        log.exception("Weekly recap refresh failed")
                try:
                    self._recap_visible = bool(self.recap.should_show())  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - optional card
                    log.exception("Weekly recap should_show failed")
                    self._recap_visible = False
            self.recap.setVisible(self._recap_visible and not self._recap_dismissed)
        self._sync_banner_box()

    def _sync_banner_box(self) -> None:
        banners = [self.nudge_banner, self.carryover_banner] + ([self.recap] if self.recap is not None else [])
        self.banner_box.setVisible(any(b.isVisibleTo(self.banner_box) for b in banners))

    def carryover_tasks(self) -> list[PlannedTask]:
        """Unfinished tasks planned before today."""
        return self._ctx.repo.unfinished_before(self._ctx.today())

    def move_carryover_to_today(self) -> None:
        """Move every unfinished earlier task to today (appended)."""
        ids = [t.id for t in self.carryover_tasks()]
        if ids:
            self._ctx.repo.move_tasks_to_date(ids, self._ctx.today())
            self._ctx.events.data_changed.emit("tasks")
        self.carryover_banner.hide()
        self._sync_banner_box()

    def dismiss_carryover(self) -> None:
        """Hide the carry-over banner for the rest of the logical day."""
        self._ctx.repo.kv_set(CARRYOVER_KEY, to_date_str(self._ctx.today()))
        self.carryover_banner.hide()
        self._sync_banner_box()

    def _on_recap_dismissed(self) -> None:
        self._recap_dismissed = True
        self._recap_visible = False
        if self.recap is not None:
            self.recap.hide()
        self._sync_banner_box()

    # ------------------------------------------------------------------ firm nudge banner

    def set_nudge_banner(self, visible: bool) -> None:
        """Show/hide the firm-nudge banner ("You're between blocks …")."""
        self._nudge_visible = bool(visible)
        self._refresh_nudge_banner()

    def nudge_banner_visible(self) -> bool:
        """True while the firm-nudge banner is requested."""
        return self._nudge_visible

    def _refresh_nudge_banner(self) -> None:
        if not self._nudge_visible:
            self.nudge_banner.hide()
            self._sync_banner_box()
            return
        label, callback = self._primary_action()
        self.nudge_banner.set_text(NUDGE_TEXT)
        self.nudge_banner.set_actions(
            [
                (label, lambda: self._nudge_act(callback)),
                ("Snooze ▾", self._open_snooze_menu),
                ("Done for today", self._nudge_done),
            ]
        )
        self.nudge_banner.show()
        self._sync_banner_box()

    def _primary_action(self) -> tuple[str, Callable[[], object]]:
        nudges = self._ctx.nudges
        if nudges is not None:
            try:
                label, cb = nudges.primary_action()
                return str(label), cb
            except Exception:  # noqa: BLE001 - fall back to "Plan now"
                log.exception("NudgeService.primary_action failed")
        return "Plan now", self.plan_card.focus_task

    def _nudge_act(self, callback: Callable[[], object]) -> None:
        self.set_nudge_banner(False)
        callback()

    def _nudge_done(self) -> None:
        self.set_nudge_banner(False)
        if self._ctx.nudges is not None:
            self._ctx.nudges.done_for_today()

    def _snooze(self, minutes: int) -> None:
        self.set_nudge_banner(False)
        if self._ctx.nudges is not None:
            self._ctx.nudges.snooze(minutes)

    def _meeting(self, minutes: int) -> None:
        self.set_nudge_banner(False)
        self._ctx.engine.start_break(minutes, "Meeting")

    def snooze_menu(self) -> Menu:
        """The Snooze ▾ menu (15 min · 30 min · 1 hour · In a meeting…)."""
        menu = Menu(self, delete_on_hide=True)
        menu.add_action("15 min", lambda: self._snooze(15), icon="bell_off")
        menu.add_action("30 min", lambda: self._snooze(30))
        menu.add_action("1 hour", lambda: self._snooze(60))
        menu.add_separator()
        meeting = menu.add_menu("In a meeting…", icon="calendar")
        meeting.add_action("30 min", lambda: self._meeting(30))
        meeting.add_action("1 hour", lambda: self._meeting(60))
        return menu

    def _open_snooze_menu(self) -> None:
        buttons = self.nudge_banner.action_buttons()
        menu = self.snooze_menu()
        self._snooze_menu = menu
        if len(buttons) > 1:
            menu.popup_below(buttons[1])
        else:
            menu.popup(self.nudge_banner.mapToGlobal(QPoint(0, self.nudge_banner.height())))

    # ------------------------------------------------------------------ flows

    def on_returned_to_idle(self, draft: tuple[str, int] | None = None) -> None:
        """A session/break ended: prefill the plan card from Up next (else clear it).

        *draft* — (title, minutes) the user typed into the break's "Next block" — is kept
        instead (see :meth:`PlanCard.set_draft`).
        """
        self.mark_dirty(("upnext", "plan", "header", "today"))
        if "upnext" in self._dirty:
            self.up_next.refresh()
            self._dirty.discard("upnext")
        if draft is not None:
            self.plan_card.set_draft(*draft)
        else:
            self.plan_card.prefill(self.up_next.first_unfinished())

    def open_wrap_up(self) -> None:
        """Open the WrapUpSheet."""
        from madhura.ui.overlays.wrap_up import open_wrap_up

        open_wrap_up(self._ctx, show_main=False)

    def flush_pending(self) -> None:
        """Commit pending undo actions now."""
        self.undo.flush()
