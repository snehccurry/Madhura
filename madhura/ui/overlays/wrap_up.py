"""WrapUpSheet — "Wrap up the day" (spec §1.9).

(a) today's focus vs goal, sessions and habits x/y; (b) unfinished Up next rows, each
[Tomorrow] (plan_date = next work day) / [Drop], plus "Move all to tomorrow"; (c) open parking-lot
captures, each [→ Tomorrow's Up next] / [Clear]; (d) an optional "One thing that went well"
(``repo.set_day_note``); (e) "Tomorrow's first block" → a task at position 0 for
``core.days.next_work_day``. Primary "Shutdown complete" saves (d)/(e), calls
``ctx.nudges.done_for_today()`` and closes.

Row actions apply immediately (with an Undo toast) and emit ``data_changed('tasks'|'captures')``.
When the next work day is not tomorrow (e.g. on a Friday) the copy names it ("Monday").
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from madhura.core import analytics as an
from madhura.core.days import next_work_day
from madhura.core.models import Capture, PlannedTask
from madhura.core.timefmt import WEEKDAYS_LONG, fmt_date_long, fmt_date_short, fmt_duration, fmt_preset
from madhura.ui import fonts, icons, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import Button, LinkButton
from madhura.ui.widgets.inputs import TextField
from madhura.ui.widgets.labels import Badge, Caption, Label, Micro, SectionHeader
from madhura.ui.widgets.list_row import ListRow
from madhura.ui.widgets.sheet import SheetContent

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["WrapUpSheet", "next_day_word", "open_wrap_up"]

log = logging.getLogger(__name__)

UNDO_MS = 5000


def next_day_word(today: date, next_day: date) -> str:
    """``"tomorrow"`` when *next_day* is the day after *today*, else its weekday (``"Monday"``)."""
    return "tomorrow" if next_day == today + timedelta(days=1) else WEEKDAYS_LONG[next_day.weekday()]


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:]


def _quote(text: str, limit: int = 32) -> str:
    text = " ".join((text or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"‘{text}’"


# =========================================================================== summary strip


class _Stat(QWidget):
    """Micro label + value (+ muted suffix) + caption."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.label = Micro(label, self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.value = QLabel("—", self)
        self.value.setFont(fonts.font(20, 600, tabular=True))
        self.suffix = QLabel("", self)
        self.suffix.setFont(fonts.font(13, 500, tabular=True))
        self.suffix.setProperty("tone", "muted")
        row.addWidget(self.value, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addWidget(self.suffix, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addStretch(1)
        lay.addWidget(self.label)
        lay.addLayout(row)

    def set(self, value: str, suffix: str = "", tone: str | None = None) -> None:
        self.value.setText(value)
        self.suffix.setText(suffix)
        self.suffix.setVisible(bool(suffix))
        theme.set_prop(self.value, "tone", tone or "")
        self.setAccessibleName(f"{self.label.text().capitalize()}: {value}{(' ' + suffix) if suffix else ''}")


class _GoalBar(QWidget):
    """Thin goal progress bar under the focus figure (success colour once met)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fraction = 0.0
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.current().changed.connect(self.update)

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self.update()

    def fraction(self) -> float:
        return self._fraction

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        r = QRectF(self.rect())
        fill_rounded(p, r, 2.0, pal.track)
        if self._fraction > 0:
            color = pal.success if self._fraction >= 1.0 else pal.accent
            fill_rounded(p, QRectF(r.left(), r.top(), max(4.0, r.width() * self._fraction), r.height()), 2.0, color)
        p.end()


class _SummaryStrip(QWidget):
    """Rounded surface_alt panel: Focus vs goal · Sessions · Habits."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(0)
        focus_box = QWidget(self)
        fl = QVBoxLayout(focus_box)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)
        self.focus = _Stat("Focus", focus_box)
        self.goal_bar = _GoalBar(focus_box)
        fl.addWidget(self.focus)
        fl.addWidget(self.goal_bar)
        self.sessions = _Stat("Sessions", self)
        self.habits = _Stat("Habits", self)
        lay.addWidget(focus_box, 3)
        lay.addSpacing(24)
        lay.addWidget(self.sessions, 2, Qt.AlignmentFlag.AlignTop)
        lay.addSpacing(8)
        lay.addWidget(self.habits, 2, Qt.AlignmentFlag.AlignTop)
        theme.current().changed.connect(self.update)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        fill_rounded(p, QRectF(self.rect()), 12.0, pal.surface_alt, None if pal.is_dark else pal.border)
        p.end()


# =========================================================================== rows


class _ActionRow(ListRow):
    """Title (elided) + optional badge + two small buttons."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(36, parent, separator=True, clickable=False, padding=(4, 0))
        self._layout.setSpacing(10)
        self.trailing_layout.setSpacing(6)
        self.title = self.set_title(title, "body")

    def add_button(self, text: str, variant: str, callback: Callable[[], None], *, icon: str | None = None,
                   accessible: str | None = None) -> Button:
        btn = Button(text, variant, icon=icon, size="sm", parent=self)
        btn.clicked.connect(lambda _checked=False: callback())
        if accessible:
            btn.setAccessibleName(accessible)
        self.add_trailing(btn)
        return btn


# =========================================================================== sheet


class WrapUpSheet(SheetContent):
    """End-of-day review: today's summary, loose ends, a note and tomorrow's first block."""

    sheet_width = 520

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._today = ctx.today()
        self._next_day = next_work_day(self._today, int(ctx.settings.get("work_days")))
        self._word = next_day_word(self._today, self._next_day)
        presets = [int(m) for m in ctx.settings.get("duration_presets")] or [25, 45, 60]
        default = int(ctx.settings.get("default_minutes"))
        self._presets = sorted(set(presets + [default]))
        self._first_minutes = default
        self.task_rows: list[_ActionRow] = []
        self.capture_rows: list[_ActionRow] = []
        self.set_title("Wrap up the day", fmt_date_long(self._today))

        # (a) summary
        self.summary = _SummaryStrip(self)
        self.body.addWidget(self.summary)
        self.body.addSpacing(12)

        # (b) unfinished Up next
        self.move_all_button = LinkButton(f"Move all to {self._word}", self, tone="accent", weight=600)
        self.move_all_button.clicked.connect(self.move_all_to_next_day)
        self.tasks_header = SectionHeader("Unfinished", None, self.move_all_button, self)
        self.tasks_box = QWidget(self)
        self._tasks_layout = QVBoxLayout(self.tasks_box)
        self._tasks_layout.setContentsMargins(0, 0, 0, 0)
        self._tasks_layout.setSpacing(0)
        self.body.addWidget(self.tasks_header)
        self.body.addWidget(self.tasks_box)

        # (c) parking lot
        self.captures_header = SectionHeader("Parking lot", None, None, self)
        self.captures_box = QWidget(self)
        self._captures_layout = QVBoxLayout(self.captures_box)
        self._captures_layout.setContentsMargins(0, 0, 0, 0)
        self._captures_layout.setSpacing(0)
        self.body.addWidget(self.captures_header)
        self.body.addWidget(self.captures_box)

        self.all_clear = QWidget(self)
        acl = QHBoxLayout(self.all_clear)
        acl.setContentsMargins(0, 2, 0, 2)
        acl.setSpacing(8)
        self._all_clear_icon = QLabel(self.all_clear)
        self._all_clear_icon.setFixedSize(16, 16)
        acl.addWidget(self._all_clear_icon)
        acl.addWidget(Caption("Nothing left hanging — Up next and the parking lot are clear.", self.all_clear), 1)
        self.body.addWidget(self.all_clear)
        self.body.addSpacing(8)

        # (d) one thing that went well
        self.body.addWidget(self._field_label("One thing that went well", "optional"))
        self.went_well = TextField("e.g. Finished the draft before lunch", None, 40, self)
        self.went_well.line_edit.setAccessibleName("One thing that went well")
        self.went_well.line_edit.setMaxLength(280)
        try:
            self.went_well.set_text(ctx.repo.get_day_note(self._today) or "")
        except sqlite3.Error:
            log.debug("Could not read today's note", exc_info=True)
        self.body.addWidget(self.went_well)
        self.body.addSpacing(4)

        # (e) tomorrow's first block
        self.first_label = self._field_label(f"{_cap(self._word)}'s first block", "")
        self.body.addWidget(self.first_label)
        self.first_block = TextField("What will you start with?", "target", 40, self)
        self.first_block.line_edit.setAccessibleName(f"{_cap(self._word)}'s first block")
        self.first_minutes = Badge(fmt_preset(self._first_minutes), "neutral", clickable=True)
        self.first_minutes.setToolTip("Length — click to change")
        self.first_minutes.setAccessibleName(f"Length {fmt_preset(self._first_minutes)}")
        self.first_minutes.clicked.connect(self.cycle_first_minutes)
        self.first_block.set_trailing_widget(self.first_minutes)
        self.body.addWidget(self.first_block)
        when = "tomorrow" if self._word == "tomorrow" else f"on {fmt_date_short(self._next_day)}"
        self.first_hint = Caption(f"It goes to the top of Up next {when}.", self)
        self.body.addWidget(self.first_hint)

        self.went_well.returnPressed.connect(lambda: self.first_block.setFocus(Qt.FocusReason.OtherFocusReason))
        self.first_block.returnPressed.connect(self.shutdown)

        # footer
        self.cancel_button = Button("Not now", "ghost", parent=self)
        self.cancel_button.clicked.connect(self.close_sheet)
        self.done_button = Button("Shutdown complete", "primary", icon="power", parent=self)
        self.done_button.clicked.connect(self.shutdown)
        self.add_footer_button(self.cancel_button)
        self.add_footer_button(self.done_button, primary=True)

        theme.current().changed.connect(self._sync_icons)
        self._sync_icons()
        self.refresh()

    # ------------------------------------------------------------------ helpers
    def _field_label(self, text: str, hint: str) -> QWidget:
        box = QWidget(self)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(Label(text, "label", box))
        if hint:
            lay.addWidget(Caption(hint, box, tone="subtle"))
        lay.addStretch(1)
        return box

    def _sync_icons(self) -> None:
        pal = theme.palette()
        self._all_clear_icon.setPixmap(icons.pixmap("check_circle", 16, pal.success, self.devicePixelRatioF()))

    def _relayout(self) -> None:
        host = self.host()
        relayout = getattr(host, "relayout", None)
        if callable(relayout):
            relayout()

    def _toast(self, text: str, undo: Callable[[], None] | None = None, kind: str = "info") -> None:
        window = self._ctx.window
        if window is None:
            return
        if undo is None:
            window.show_toast(text, kind)
        else:
            window.show_toast(text, kind, "Undo", undo, UNDO_MS)

    def _changed(self, *kinds: str) -> None:
        for kind in kinds:
            self._ctx.events.data_changed.emit(kind)

    # ------------------------------------------------------------------ data
    @property
    def next_day(self) -> date:
        """The next work day (where "Tomorrow" moves things)."""
        return self._next_day

    def unfinished_tasks(self) -> list[PlannedTask]:
        """Carried-over (earlier days) then today's not-done Up next tasks."""
        repo = self._ctx.repo
        return repo.unfinished_before(self._today) + repo.tasks_for_date(self._today, include_done=False)

    def open_captures(self) -> list[Capture]:
        """Parking-lot captures not yet cleared."""
        return self._ctx.repo.open_captures()

    def refresh(self) -> None:
        """Reload the summary and the rows from the database."""
        try:
            self._fill_summary()
            self._fill_tasks()
            self._fill_captures()
        except sqlite3.Error:
            log.exception("Could not load the wrap-up data")
        has_any = bool(self.task_rows or self.capture_rows)
        self.all_clear.setVisible(not has_any)
        self._relayout()

    def _fill_summary(self) -> None:
        ctx = self._ctx
        start, end = ctx.day_bounds(self._today)
        sessions = ctx.repo.sessions_between(start, end)
        focused = an.total_seconds(sessions)
        goal = int(ctx.settings.get("daily_goal_minutes")) * 60
        met = goal > 0 and focused >= goal
        self.summary.focus.set(fmt_duration(focused), f"/ {fmt_duration(goal)}" if goal else "", "success" if met else None)
        self.summary.goal_bar.set_fraction(focused / goal if goal else 0.0)
        self.summary.sessions.set(str(len(an.counted(sessions))))
        done, scheduled = an.habit_day_counts(ctx.repo.habits(), ctx.repo.habit_logs_for_date(self._today), self._today)
        if scheduled:
            self.summary.habits.set(str(done), f"/ {scheduled}", "success" if done >= scheduled else None)
        else:
            self.summary.habits.set("—", "none today")

    @staticmethod
    def _clear(layout: QVBoxLayout, rows: list[_ActionRow]) -> None:
        for row in rows:
            layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        rows.clear()

    def _fill_tasks(self) -> None:
        self._clear(self._tasks_layout, self.task_rows)
        tasks = self.unfinished_tasks()
        word = _cap(self._word)
        for task in tasks:
            row = _ActionRow(task.title, self.tasks_box)
            badge = Badge(fmt_preset(task.minutes), "neutral", row)
            row.add_trailing(badge)
            if task.plan_date < self._today:
                row.title.setToolTip(f"{task.title}\nPlanned for {fmt_date_short(task.plan_date)}")
            row.add_button(word, "secondary", lambda t=task: self.move_to_next_day(t.id),
                           accessible=f"Move {_quote(task.title)} to {self._word}")
            row.add_button("Drop", "ghost", lambda t=task: self.drop_task(t.id), accessible=f"Drop {_quote(task.title)}")
            row.task_id = task.id  # type: ignore[attr-defined]
            self._tasks_layout.addWidget(row)
            self.task_rows.append(row)
        if self.task_rows:
            self.task_rows[-1].set_separator(False)
        self.tasks_header.set_count(len(tasks) or None)
        self.tasks_header.setVisible(bool(tasks))
        self.tasks_box.setVisible(bool(tasks))
        self.move_all_button.setVisible(len(tasks) > 1)

    def _fill_captures(self) -> None:
        self._clear(self._captures_layout, self.capture_rows)
        caps = self.open_captures()
        for cap in caps:
            row = _ActionRow(cap.text, self.captures_box)
            row.add_button(f"{_cap(self._word)}'s Up next", "secondary", lambda c=cap: self.capture_to_next_day(c.id),
                           icon="arrow_right", accessible=f"Add {_quote(cap.text)} to {self._word}'s Up next")
            row.add_button("Clear", "ghost", lambda c=cap: self.clear_capture(c.id), accessible=f"Clear {_quote(cap.text)}")
            row.capture_id = cap.id  # type: ignore[attr-defined]
            self._captures_layout.addWidget(row)
            self.capture_rows.append(row)
        if self.capture_rows:
            self.capture_rows[-1].set_separator(False)
        self.captures_header.set_count(len(caps) or None)
        self.captures_header.setVisible(bool(caps))
        self.captures_box.setVisible(bool(caps))

    # ------------------------------------------------------------------ task actions
    def move_to_next_day(self, task_id: int) -> None:
        """[Tomorrow]: move one task to the next work day (undoable)."""
        repo = self._ctx.repo
        task = repo.get_task(task_id)
        if task is None:
            return
        repo.move_tasks_to_date([task_id], self._next_day)
        self._changed("tasks")
        self.refresh()

        def undo() -> None:
            repo.update_task(task_id, plan_date=task.plan_date, position=task.position)
            self._changed("tasks")
            self.refresh()

        self._toast(f"Moved {_quote(task.title)} to {self._word}", undo)

    def move_all_to_next_day(self) -> None:
        """"Move all to tomorrow" (undoable)."""
        repo = self._ctx.repo
        tasks = self.unfinished_tasks()
        if not tasks:
            return
        repo.move_tasks_to_date([t.id for t in tasks], self._next_day)
        self._changed("tasks")
        self.refresh()

        def undo() -> None:
            with repo.transaction():
                for t in tasks:
                    repo.update_task(t.id, plan_date=t.plan_date, position=t.position)
            self._changed("tasks")
            self.refresh()

        n = len(tasks)
        self._toast(f"Moved {n} task{'s' if n != 1 else ''} to {self._word}", undo)

    def drop_task(self, task_id: int) -> None:
        """[Drop]: delete an unfinished task (undo re-creates it in place)."""
        repo = self._ctx.repo
        task = repo.get_task(task_id)
        if task is None:
            return
        repo.delete_task(task_id)
        self._changed("tasks")
        self.refresh()

        def undo() -> None:
            new_id = repo.add_task(task.title, task.minutes, task.project, plan_date=task.plan_date, position=task.position)
            repo.update_task(new_id, created_at=task.created_at)
            self._changed("tasks")
            self.refresh()

        self._toast(f"Dropped {_quote(task.title)}", undo)

    # ------------------------------------------------------------------ capture actions
    def capture_to_next_day(self, capture_id: int) -> None:
        """[→ Tomorrow's Up next]: turn a capture into a task for the next work day and clear it."""
        repo = self._ctx.repo
        cap = next((c for c in repo.open_captures() if c.id == capture_id), None)
        if cap is None:
            return
        with repo.transaction():
            task_id = repo.add_task(cap.text, 25, None, plan_date=self._next_day)
            repo.set_capture_done(capture_id, True)
        self._changed("tasks", "captures")
        self.refresh()

        def undo() -> None:
            with repo.transaction():
                repo.delete_task(task_id)
                repo.set_capture_done(capture_id, False)
            self._changed("tasks", "captures")
            self.refresh()

        self._toast(f"Added to {self._word}'s Up next", undo)

    def clear_capture(self, capture_id: int) -> None:
        """[Clear]: mark a capture done (undoable)."""
        repo = self._ctx.repo
        repo.set_capture_done(capture_id, True)
        self._changed("captures")
        self.refresh()

        def undo() -> None:
            repo.set_capture_done(capture_id, False)
            self._changed("captures")
            self.refresh()

        self._toast("Cleared from the parking lot", undo)

    # ------------------------------------------------------------------ first block
    def first_block_minutes(self) -> int:
        """Length of tomorrow's first block (minutes)."""
        return self._first_minutes

    def cycle_first_minutes(self) -> None:
        """Click on the length badge: next preset (wraps around)."""
        presets = self._presets
        later = [m for m in presets if m > self._first_minutes]
        self._first_minutes = later[0] if later else presets[0]
        self.first_minutes.set_text(fmt_preset(self._first_minutes))
        self.first_minutes.setAccessibleName(f"Length {fmt_preset(self._first_minutes)}")

    # ------------------------------------------------------------------ finish
    def shutdown(self) -> None:
        """"Shutdown complete": save the note and first block, done for today, close."""
        ctx = self._ctx
        repo = ctx.repo
        try:
            note = self.went_well.text().strip()
            if note or repo.get_day_note(self._today):
                repo.set_day_note(self._today, note or None)
            first = self.first_block.text().strip()
            if first:
                repo.add_task(first, self._first_minutes, None, plan_date=self._next_day, position=0)
                self._changed("tasks")
        except sqlite3.Error:
            log.exception("Could not save the wrap-up")
        if ctx.nudges is not None:
            try:
                ctx.nudges.done_for_today()
            except Exception:  # noqa: BLE001 - the nudge service is another feature; never block closing
                log.exception("done_for_today failed")
        when = "tomorrow" if self._word == "tomorrow" else self._word
        self._toast(f"Shutdown complete — see you {when}.", kind="success")
        self.close_sheet()


def open_wrap_up(ctx: AppContext, *, show_main: bool = True) -> WrapUpSheet | None:
    """Show the WrapUpSheet (main window on the Focus page first when *show_main*).

    Every entry point (Focus page footer, tray menu, the after-work prompt) goes through here, so
    a wrap-up that is already open or queued is reused instead of stacking a second one.
    """
    window = ctx.window
    if window is None:
        return None
    if show_main:
        window.show_main("focus", activate=True)
    host = getattr(window, "sheet_host", None)
    if host is not None:
        for sheet in (host.current(), *host.queued()):
            if isinstance(sheet, WrapUpSheet):
                return sheet
    sheet = WrapUpSheet(ctx)
    window.show_sheet(sheet)
    return sheet
