"""HabitsPage — daily habits, Atomic-Habits style (spec §1.8).

Layout (max width 760)::

    Habits                                              [+ New habit]
    Improve by 1% every day — …37x growth.
    ┌ today summary (88 px): "3 of 5" done today ·············· ▬▬▬▬▭ ┐
    ┌ one card ─────────────────────────────────────────────────────────┐
    │ MORNING                                                           │
    │ ◯ 🧘 Meditate                                    ●●●○●●◌  🔥6  ⋯ │
    │      After coffee · 07:00                                         │
    │ AFTERNOON … EVENING … ANYTIME …                                   │
    │ ▸ Not today  2                                                    │
    └───────────────────────────────────────────────────────────────────┘
    ▸ Archived habits  1

Rows: 28 px check circle (36 px hit), emoji, name 14/600 + caption, last-7-days dots, streak chip,
⋯ on hover (Edit · Skip today/Unskip · Archive · Delete…). Keyboard on a row: ↑/↓ move, Space done,
S skip, E/Enter edit, Delete delete, Menu/Shift+F10 actions. Empty state with the six suggestions.

Everything "today" is the *logical* day (``ctx.today()``). The page writes habit logs itself
(``repo.set_habit_status`` + ``events.data_changed('habits')``) and refreshes in place, so focus
and selection survive a toggle.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from madhura.core import habits as hcore
from madhura.core.models import Habit, HabitLog
from madhura.core.timefmt import WEEKDAYS_LONG
from madhura.ui import dialogs, fonts
from madhura.ui.pages._habit_kit import (
    CheckCircle,
    DotStrip,
    HabitGlyph,
    SegmentBar,
    StreakChip,
    fmt_hhmm,
)
from madhura.ui.pages.habit_editor import SUGGESTIONS, HabitEditorSheet
from madhura.ui.widgets.buttons import Button, IconButton
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.chips import Chip
from madhura.ui.widgets.empty_state import EmptyState
from madhura.ui.widgets.labels import Badge, ElidedLabel, Label, SectionHeader
from madhura.ui.widgets.list_row import ListRow
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.page import PageHeader, PageScroll
from madhura.ui.widgets.settings_row import Divider
from madhura.ui.widgets.sheet import ConfirmSheet

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["HabitsPage", "HabitRow", "RowModel", "MOTTO", "EMPTY_TITLE"]

log = logging.getLogger(__name__)

MOTTO = "Improve by 1% every day — by the end of the year, you'll reach ~37x growth."
EMPTY_TITLE = "Small habits, big results"
_REFRESH_MS = 60_000
_KEYBOARD_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
    Qt.FocusReason.OtherFocusReason,
)


# =========================================================================== model


@dataclass
class RowModel:
    """Everything a :class:`HabitRow` shows for one habit today."""

    habit: Habit
    group: str
    scheduled: bool
    #: "done" | "skipped" | "none"
    state: str
    caption: str
    #: None (muted) | "warning"
    caption_tone: str | None
    last7: list[tuple[date, str]] = field(default_factory=list)
    streak: int = 0
    best: int = 0
    auto_minutes: int | None = None
    pending: bool = False
    missed_previous: bool = False


def _short(text: str, limit: int = 32) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_models(ctx: AppContext) -> tuple[list[RowModel], list[Habit]]:
    """Row models for the active habits (page order) and the archived habits."""
    repo = ctx.repo
    today = ctx.today()
    now = ctx.clock.now()
    dsh = ctx.day_start_hour()
    use_24h = ctx.use_24h()
    everything = repo.habits(include_archived=True)
    habits = [h for h in everything if not h.archived]
    archived = [h for h in everything if h.archived]
    if not habits:
        return [], archived
    start = min((hcore.created_date(h) or today) for h in habits)
    # one extra day: a habit created just after midnight may hold a log for the previous logical day
    start = min(start, today - timedelta(days=7)) - timedelta(days=1)
    by_habit: dict[int, list[HabitLog]] = defaultdict(list)
    for lg in repo.habit_logs_between(start, today):
        by_habit[lg.habit_id].append(lg)
    sessions: list[Any] = []
    if any(h.is_auto for h in habits):
        sessions = repo.sessions_between(*ctx.day_bounds(today))
    models: list[RowModel] = []
    for h in sorted(habits, key=lambda x: hcore.sort_key(x, day_start_hour=dsh)):
        logs = by_habit.get(h.id, [])
        status = next((str(lg.status) for lg in logs if lg.date == today), None)
        state = status if status in ("done", "skipped") else "none"
        scheduled = hcore.is_scheduled(h, today)
        current, best = hcore.habit_streak(h, logs, today)
        auto_minutes = hcore.focused_minutes(sessions, h.project) if h.is_auto else None
        due = hcore.due_at(h, today, dsh)
        pending = scheduled and state == "none" and due is not None and now >= due
        missed = scheduled and state == "none" and hcore.missed_yesterday(h, logs, today)
        caption, tone = _caption(h, state, scheduled, auto_minutes, pending, missed, today, use_24h)
        models.append(
            RowModel(
                habit=h,
                group=hcore.group_of(h, day_start_hour=dsh),
                scheduled=scheduled,
                state=state,
                caption=caption,
                caption_tone=tone,
                last7=hcore.last_7(h, logs, today),
                streak=current,
                best=best,
                auto_minutes=auto_minutes,
                pending=pending,
                missed_previous=missed,
            )
        )
    return models, archived


def _caption(
    h: Habit,
    state: str,
    scheduled: bool,
    auto_minutes: int | None,
    pending: bool,
    missed: bool,
    today: date,
    use_24h: bool,
) -> tuple[str, str | None]:
    """Caption text + tone for a row (see spec §1.8 for the wording)."""
    cue = (h.cue or "").strip()
    when = fmt_hhmm(h.time, use_24h) if h.time else "Anytime"
    if not scheduled:
        parts = [p for p in (cue, hcore.days_label(h.days), fmt_hhmm(h.time, use_24h) if h.time else "") if p]
        return " · ".join(parts), None
    if state == "skipped":
        return "Skipped today", None
    if h.is_auto and auto_minutes is not None:
        target = int(h.focus_minutes or 0)
        what = f"on {h.project.strip()}" if (h.project or "").strip() else "deep work"
        progress = f"{auto_minutes} / {target} min {what}"
        return (f"{cue} · {progress}" if cue else progress), None
    if missed:
        prev = hcore.previous_scheduled_day(h, today)
        when_missed = "yesterday" if prev == today - timedelta(days=1) or prev is None else WEEKDAYS_LONG[prev.weekday()]
        return f"Missed {when_missed} · don't miss twice", "warning"
    parts = [p for p in (cue, when) if p]
    if h.time and not h.reminder:
        parts.append("reminder off")
    if pending:
        parts.append("Pending")
    return " · ".join(parts), None


# =========================================================================== row


class HabitRow(ListRow):
    """One habit row (60 px). Emits requests; the page performs them."""

    toggle_requested = Signal(int)
    skip_requested = Signal(int)
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    menu_requested = Signal(int)
    navigate_requested = Signal(int, int)  # habit id, step (+1 down / -1 up / ±999 ends)

    def __init__(self, model: RowModel, parent: QWidget | None = None) -> None:
        super().__init__(60, parent, separator=False, clickable=True, padding=(12, 8), focusable=True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.habit_id = model.habit.id
        self.model = model
        self.check = CheckCircle(self)
        self.check.clicked.connect(lambda _c=False: self.toggle_requested.emit(self.habit_id))
        self.add_leading(self.check)
        self.glyph = HabitGlyph(model.habit.emoji, model.habit.name, 28, self)
        self.add_leading(self.glyph)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self.name_label = ElidedLabel(model.habit.name, "habit_name", self)
        self.name_label.setFont(fonts.font(14, 600))
        # Preferred (not Ignored): sized to the text when there is room so the badge follows
        # the name, shrinking down to the minimum (eliding) when there is not.
        self.name_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        title_row.addWidget(self.name_label, 0)
        self.auto_badge = Badge("Auto", "accent", self, icon="target")
        self.auto_badge.setToolTip("Done automatically when today's focus time reaches the target")
        title_row.addWidget(self.auto_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        self.content_layout.addLayout(title_row)
        self.caption_label = ElidedLabel("", "caption", self)
        self.add_content(self.caption_label)

        self.dots = DotStrip(self)
        self.add_trailing(self.dots)
        self.streak = StreakChip(self)
        self.add_trailing(self.streak)
        self.more = IconButton("more", "More actions", 28, self, accessible_name=f"More actions for {model.habit.name}")
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.clicked.connect(lambda: self.menu_requested.emit(self.habit_id))
        self.add_trailing(self.more, hover_only=True)
        self.activated.connect(lambda: self.edit_requested.emit(self.habit_id))
        self.apply(model)

    # ------------------------------------------------------------------ model
    def apply(self, model: RowModel) -> None:
        """Show *model* (in place)."""
        self.model = model
        h = model.habit
        self.check.set_state(model.state)
        done = model.state == "done"
        verb = "Mark not done" if done else "Mark done"
        self.check.setAccessibleName(f"{verb}: {h.name}")
        self.check.setToolTip(("Done today — click to undo" if done else "Mark done today") + " · Space")
        self.glyph.set_habit(h.emoji, h.name)
        self.glyph.set_muted(done or not model.scheduled)
        self.name_label.set_text(h.name)
        self.name_label.set_tone("muted" if (done or not model.scheduled) else None)
        self.auto_badge.setVisible(h.is_auto)
        self.caption_label.set_text(model.caption)
        self.caption_label.set_tone(model.caption_tone or "")
        self.dots.set_days(model.last7)
        self.streak.set_streak(model.streak, model.best)
        status = {"done": "done today", "skipped": "skipped today"}.get(model.state, "not done yet")
        if not model.scheduled:
            status = "not scheduled today"
        self.setAccessibleName(h.name)
        self.setAccessibleDescription(f"{status}. {model.caption}")

    # ------------------------------------------------------------------ keyboard / focus
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        plain = mods == Qt.KeyboardModifier.NoModifier
        hid = self.habit_id
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and plain:
            self.navigate_requested.emit(hid, 1 if key == Qt.Key.Key_Down else -1)
        elif key in (Qt.Key.Key_Home, Qt.Key.Key_End) and plain:
            self.navigate_requested.emit(hid, -999 if key == Qt.Key.Key_Home else 999)
        elif key == Qt.Key.Key_Space and plain:
            self.toggle_requested.emit(hid)
        elif key == Qt.Key.Key_S and plain:
            self.skip_requested.emit(hid)
        elif (key == Qt.Key.Key_E and plain) or (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain):
            self.edit_requested.emit(hid)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and plain:
            self.delete_requested.emit(hid)
        elif key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and mods == Qt.KeyboardModifier.ShiftModifier):
            self.menu_requested.emit(hid)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event: Any) -> None:  # noqa: N802
        if event.reason() in _KEYBOARD_REASONS:
            self.set_selected(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.set_selected(False)
        super().focusOutEvent(event)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        # Clicking the row body focuses it quietly (no keyboard highlight).
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)


# =========================================================================== summary card


class _SummaryCard(Card):
    """Today summary (88 px): "3 of 5" + "done today" · 240×8 segment bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(padding=(24, 0, 24, 0), parent=parent)
        self.setFixedHeight(88)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        left.addStretch(1)
        nums = QHBoxLayout()
        nums.setContentsMargins(0, 0, 0, 0)
        nums.setSpacing(8)
        self.done_label = Label("0", "summary_value", self)
        self.done_label.setFont(fonts.font(28, 600, tabular=True))
        self.of_label = Label("of 0", "summary_value", self, tone="muted")
        self.of_label.setFont(fonts.font(28, 600, tabular=True))
        self.title_label = Label("", "summary_title", self)
        self.title_label.setFont(fonts.font(16, 600))
        nums.addWidget(self.done_label, 0, Qt.AlignmentFlag.AlignBaseline)
        nums.addWidget(self.of_label, 0, Qt.AlignmentFlag.AlignBaseline)
        nums.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        nums.addStretch(1)
        left.addLayout(nums)
        self.caption_label = Label("done today", "caption", self)
        left.addWidget(self.caption_label)
        left.addStretch(1)
        row.addLayout(left, 1)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addStretch(1)
        self.bar = SegmentBar(self)
        right.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignRight)
        self.bar_caption = Label("", "caption", self)
        self.bar_caption.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.bar_caption, 0, Qt.AlignmentFlag.AlignRight)
        right.addStretch(1)
        row.addLayout(right)
        self.body.addLayout(row)
        self.setAccessibleName("Today")

    def set_counts(self, states: list[str]) -> None:
        """Update from the per-habit states of today's scheduled habits ("done"/"skipped"/"none")."""
        total = len(states)
        done = sum(1 for s in states if s == "done")
        skipped = sum(1 for s in states if s == "skipped")
        counted = total - skipped
        # Progress fills from the left: done, then skipped, then what's left to do.
        order = {"done": 0, "skipped": 1}
        self.bar.set_states(sorted(states, key=lambda st: order.get(st, 2)))
        if total == 0:
            self.done_label.hide()
            self.of_label.hide()
            self.title_label.setText("Nothing scheduled today")
            self.title_label.show()
            self.caption_label.setText("Enjoy the breather — tomorrow's habits will be here.")
            self.caption_label.set_tone("")
            self.bar.hide()
            self.bar_caption.hide()
            self.setAccessibleDescription("Nothing scheduled today")
            return
        self.bar.show()
        self.title_label.hide()
        self.done_label.show()
        self.of_label.show()
        self.done_label.setText(str(done))
        self.of_label.setText(f"of {counted}")
        left = counted - done
        if counted > 0 and left == 0:
            self.caption_label.setText("All done for today")
            self.caption_label.set_tone("success")
        elif counted == 0:
            self.caption_label.setText("All skipped today — rest is part of the plan")
            self.caption_label.set_tone("")
        else:
            self.caption_label.setText("done today")
            self.caption_label.set_tone("")
        bits = []
        if left > 0:
            bits.append(f"{left} to go")
        if skipped:
            bits.append(f"{skipped} skipped")
        self.bar_caption.setText(" · ".join(bits))
        self.bar_caption.setVisible(bool(bits))
        self.setAccessibleDescription(f"{done} of {counted} done today")

    def value_text(self) -> str:
        """"3 of 5" (or the title when nothing is scheduled)."""
        if self.done_label.isHidden():
            return self.title_label.text()
        return f"{self.done_label.text()} {self.of_label.text()}"


# =========================================================================== page


class HabitsPage(PageScroll):
    """Daily habits page (max width 760). The shell calls ``on_shown()`` and ``new_habit()``."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(760, parent)
        self._ctx = ctx
        self._dirty = True
        self._layout_key: tuple[Any, ...] | None = None
        self._rows: dict[int, HabitRow] = {}
        self._order: list[int] = []
        self._models: dict[int, RowModel] = {}
        self._archived: list[Habit] = []
        self._not_today_open = False
        self._archived_open = False
        self._menu: Menu | None = None
        self._last_today: date | None = None

        self.new_button = Button("New habit", "primary", icon="plus", parent=self.column)
        self.new_button.setToolTip("New habit · Ctrl+N")
        self.new_button.clicked.connect(lambda: self.new_habit())
        self.header = PageHeader("Habits", [self.new_button], self.column, subtitle=MOTTO)
        self.add(self.header)
        self.content_layout.addSpacing(8)  # 16 + 8 = 24 header → content

        self.summary = _SummaryCard(self.column)
        self.add(self.summary)

        self.list_card = Card(padding=(8, 8, 8, 8), parent=self.column)
        self.list_card.body.setSpacing(0)
        self.add(self.list_card)

        self.empty_card = Card(padding=(24, 8, 24, 16), parent=self.column)
        self.empty_action = Button("New habit", "primary", icon="plus", size="lg", parent=self.empty_card)
        self.empty_action.clicked.connect(lambda: self.new_habit())
        # Same line as MOTTO, broken where it reads best inside the narrow empty-state column.
        self.empty = EmptyState("sparkle", EMPTY_TITLE, MOTTO.replace(" — ", " —\n", 1), None, self.empty_card)
        self.suggestions = _SuggestionGrid(self.empty)
        self.suggestions.picked.connect(self._on_suggestion)
        self.empty.add_widget(self.suggestions)
        self.empty.add_widget(_spacer(8))
        self.empty.set_action(self.empty_action)
        self.empty_card.body.addWidget(self.empty)
        self.empty_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.add(self.empty_card)

        self.archived_toggle = Button("Archived habits", "ghost", icon="chevron_right", size="sm", parent=self.column)
        self.archived_toggle.clicked.connect(self._toggle_archived)
        self.add(self.archived_toggle)
        self.content_layout.setAlignment(self.archived_toggle, Qt.AlignmentFlag.AlignLeft)
        self.archived_card = Card(padding=(8, 8, 8, 8), parent=self.column)
        self.archived_card.body.setSpacing(0)
        self.add(self.archived_card)
        self.archived_card.hide()

        self._tick = QTimer(self)
        self._tick.setInterval(_REFRESH_MS)
        self._tick.timeout.connect(self.refresh)

        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.events.day_changed.connect(self._on_day_changed)
        self.refresh()

    # ================================================================== lifecycle
    def _on_data_changed(self, kind: str) -> None:
        if kind in ("habits", "all", "sessions", "settings"):
            self._dirty = True
            if self.isVisible():
                self.refresh()

    def _on_day_changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def on_shown(self) -> None:
        """Refresh (cheap, in place) — pending/late captions depend on the time of day."""
        self.refresh()

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        self._tick.start()

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        self._tick.stop()
        super().hideEvent(event)

    # ================================================================== building
    def refresh(self) -> None:
        """Recompute every row; rebuilds only when the grouping/order changed."""
        ctx = self._ctx
        if ctx.repo.closed:
            return
        self._dirty = False
        models, archived = build_models(ctx)
        self._archived = archived
        self._models = {m.habit.id: m for m in models}
        scheduled = [m for m in models if m.scheduled]
        not_today = [m for m in models if not m.scheduled]
        groups: list[tuple[str, list[int]]] = []
        for g in hcore.GROUPS:
            ids = [m.habit.id for m in scheduled if m.group == g]
            if ids:
                groups.append((g, ids))
        key = (tuple((g, tuple(ids)) for g, ids in groups), tuple(m.habit.id for m in not_today), self._not_today_open)
        empty = not models
        self.summary.setVisible(not empty)
        self.list_card.setVisible(not empty)
        self.empty_card.setVisible(empty)
        self.new_button.setVisible(not empty)
        # The empty state carries the 1%/37x line itself; don't say it twice. A blank line (not a
        # hidden one) keeps the title from jumping when the first habit is added.
        self.header.set_subtitle("\u00a0" if empty else MOTTO)
        shown = {hid for _g, ids in groups for hid in ids} | ({m.habit.id for m in not_today} if self._not_today_open else set())
        if key != self._layout_key or set(self._rows) != shown:
            self._rebuild(groups, not_today)
            self._layout_key = key
        else:
            for hid, row in self._rows.items():
                row.apply(self._models[hid])
        self.summary.set_counts([m.state for m in scheduled])
        self._refresh_archived()
        self._last_today = ctx.today()

    def _clear(self, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)  # type: ignore[call-overload]
                w.deleteLater()
            elif item is not None and item.layout() is not None:
                self._clear(item.layout())

    def _rebuild(self, groups: list[tuple[str, list[int]]], not_today: list[RowModel]) -> None:
        from PySide6.QtWidgets import QApplication

        fw = QApplication.focusWidget()
        focused_id = fw.habit_id if isinstance(fw, HabitRow) else None
        if fw is not None and self.list_card.isAncestorOf(fw):
            # Park focus on the page while rows are replaced, so Qt doesn't hand it (with a
            # keyboard-ring reason) to whatever widget comes next in the chain.
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        body = self.list_card.body
        self._clear(body)
        self._rows = {}
        self._order = []
        first = True
        for g, ids in groups:
            header = SectionHeader(hcore.GROUP_LABELS[g], None, None, self.list_card)
            header.setContentsMargins(16, 0 if first else 8, 16, 0)
            body.addWidget(header)
            for hid in ids:
                self._add_row(body, self._models[hid])
            first = False
        if not_today:
            if groups:
                body.addSpacing(6)
                div = Divider(self.list_card)
                body.addWidget(div)
                body.addSpacing(6)
            n = len(not_today)
            chevron = "chevron_down" if self._not_today_open else "chevron_right"
            self.not_today_toggle = Button(f"Not today ({n})", "ghost", icon=chevron, size="sm", parent=self.list_card)
            self.not_today_toggle.setToolTip("Habits not scheduled for today")
            self.not_today_toggle.setAccessibleName(f"Not today, {n} habit{'s' if n != 1 else ''}")
            self.not_today_toggle.clicked.connect(self._toggle_not_today)
            wrap = QHBoxLayout()
            wrap.setContentsMargins(4, 0, 0, 0)
            wrap.addWidget(self.not_today_toggle)
            wrap.addStretch(1)
            body.addLayout(wrap)
            if self._not_today_open:
                body.addSpacing(4)
                for m in not_today:
                    self._add_row(body, m)
        else:
            self.not_today_toggle = None  # type: ignore[assignment]
        if focused_id is not None and focused_id in self._rows:
            self._rows[focused_id].setFocus(Qt.FocusReason.OtherFocusReason)

    def _add_row(self, body: QVBoxLayout, model: RowModel) -> HabitRow:
        row = HabitRow(model, self.list_card)
        row.toggle_requested.connect(self.toggle_done)
        row.skip_requested.connect(self.toggle_skip)
        row.edit_requested.connect(self.edit_habit)
        row.delete_requested.connect(self.delete_habit)
        row.menu_requested.connect(self.open_menu)
        row.navigate_requested.connect(self._navigate)
        body.addWidget(row)
        self._rows[model.habit.id] = row
        self._order.append(model.habit.id)
        return row

    def _refresh_archived(self) -> None:
        n = len(self._archived)
        self.archived_toggle.setVisible(n > 0)
        self.archived_toggle.setText(f"Archived habits ({n})")
        self.archived_toggle.set_icon_name("chevron_down" if self._archived_open else "chevron_right")
        show = n > 0 and self._archived_open
        body = self.archived_card.body
        self._clear(body)
        if show:
            for h in self._archived:
                body.addWidget(self._archived_row(h))
        self.archived_card.setVisible(show)

    def _archived_row(self, h: Habit) -> ListRow:
        row = ListRow(52, self.archived_card, clickable=False, padding=(12, 8))
        glyph = HabitGlyph(h.emoji, h.name, 28, row)
        glyph.set_muted(True)
        row.add_leading(glyph)
        name = row.set_title(h.name)
        name.set_tone("muted")
        when = fmt_hhmm(h.time, self._ctx.use_24h()) if h.time else "Anytime"
        row.set_subtitle(f"{hcore.days_label(h.days)} · {when}")
        restore = Button("Restore", "secondary", icon="undo", size="sm", parent=row)
        restore.setAccessibleName(f"Restore {h.name}")
        restore.clicked.connect(lambda _c=False, hid=h.id: self.unarchive(hid))
        row.add_trailing(restore)
        delete = IconButton("trash", "Delete permanently", 28, row, accessible_name=f"Delete {h.name} permanently")
        delete.clicked.connect(lambda _c=False, hid=h.id: self.delete_habit(hid))
        row.add_trailing(delete)
        return row

    # ================================================================== queries (tests)
    def rows(self) -> list[HabitRow]:
        """Visible habit rows in display order."""
        return [self._rows[h] for h in self._order if h in self._rows]

    def row(self, habit_id: int) -> HabitRow | None:
        """The row of *habit_id* (None when not shown)."""
        return self._rows.get(int(habit_id))

    def model(self, habit_id: int) -> RowModel | None:
        """The current model of *habit_id*."""
        return self._models.get(int(habit_id))

    def group_titles(self) -> list[str]:
        """Section titles shown, in order."""
        return [hcore.GROUP_LABELS[g] for g, _ids in (self._layout_key or ((),))[0]] if self._layout_key else []

    def is_empty(self) -> bool:
        """True while the empty state is shown."""
        return not self.empty_card.isHidden()

    # ================================================================== actions
    def _window(self) -> Any:
        return self._ctx.window

    def new_habit(self, preset: dict[str, Any] | str | None = None) -> HabitEditorSheet | None:
        """Open the editor for a new habit (Ctrl+N on this page); *preset* pre-fills it."""
        window = self._window()
        if window is None:
            return None
        sheet = HabitEditorSheet(self._ctx, preset=preset)
        sheet.saved.connect(self._on_saved)
        window.show_sheet(sheet)
        return sheet

    def edit_habit(self, habit_id: int) -> HabitEditorSheet | None:
        """Open the editor for *habit_id*."""
        window = self._window()
        habit = self._ctx.repo.get_habit(int(habit_id))
        if window is None or habit is None:
            return None
        sheet = HabitEditorSheet(self._ctx, habit)
        sheet.saved.connect(self._on_saved)
        window.show_sheet(sheet)
        return sheet

    def _on_saved(self, habit_id: int) -> None:
        self.refresh()
        row = self._rows.get(int(habit_id))
        if row is not None:
            QTimer.singleShot(0, row, lambda: self.ensureWidgetVisible(row, 0, 24) if row.isVisible() else None)

    def _on_suggestion(self, name: str) -> None:
        self.new_habit(str(name))

    def _set_status(self, habit_id: int, status: str | None) -> None:
        ctx = self._ctx
        ctx.repo.set_habit_status(int(habit_id), ctx.today(), status, at=ctx.clock.now().replace(microsecond=0))
        ctx.events.data_changed.emit("habits")
        if not self.isVisible():
            self.refresh()

    def toggle_done(self, habit_id: int) -> None:
        """Done ↔ not done for today (a skipped habit becomes done)."""
        model = self._models.get(int(habit_id))
        if model is None:
            return
        self._set_status(habit_id, None if model.state == "done" else "done")

    def toggle_skip(self, habit_id: int) -> None:
        """Skip today ↔ unskip."""
        model = self._models.get(int(habit_id))
        if model is None:
            return
        self._set_status(habit_id, None if model.state == "skipped" else "skipped")

    def archive(self, habit_id: int) -> None:
        """Archive *habit_id* (history kept) with an Undo toast."""
        ctx = self._ctx
        habit = ctx.repo.get_habit(int(habit_id))
        if habit is None:
            return
        ctx.repo.update_habit(habit.id, archived=True)
        ctx.events.data_changed.emit("habits")
        window = self._window()
        if window is not None:
            window.show_toast(f"Archived ‘{_short(habit.name)}’", "info", "Undo", lambda hid=habit.id: self.unarchive(hid), 6000)

    def unarchive(self, habit_id: int) -> None:
        """Bring an archived habit back."""
        ctx = self._ctx
        if ctx.repo.closed or ctx.repo.get_habit(int(habit_id)) is None:
            return
        ctx.repo.update_habit(int(habit_id), archived=False)
        ctx.events.data_changed.emit("habits")

    def delete_habit(self, habit_id: int) -> None:
        """Ask (ConfirmSheet), then delete *habit_id* and its whole history."""
        ctx = self._ctx
        habit = ctx.repo.get_habit(int(habit_id))
        if habit is None:
            return
        title = f"Delete ‘{_short(habit.name)}’?"
        text = "This removes the habit and its entire history, including streaks. Archive it instead to keep the history."
        window = self._window()
        if window is None:
            if dialogs.confirm(self, title, text, "Delete habit", danger=True):
                self._delete_now(habit.id)
            return
        sheet = ConfirmSheet(title, text, "Delete habit", danger=True)
        sheet.accepted.connect(lambda hid=habit.id: self._delete_now(hid))
        window.show_sheet(sheet)

    def _delete_now(self, habit_id: int) -> None:
        ctx = self._ctx
        if ctx.repo.closed:
            return
        habit = ctx.repo.get_habit(int(habit_id))
        if habit is None:
            return
        ctx.repo.delete_habit(habit.id)
        ctx.events.data_changed.emit("habits")
        window = self._window()
        if window is not None:
            window.show_toast(f"Deleted ‘{_short(habit.name)}’", "info")

    def open_menu(self, habit_id: int) -> Menu | None:
        """The ⋯ menu of a row: Edit · Skip today/Unskip · Archive · Delete…"""
        row = self._rows.get(int(habit_id))
        model = self._models.get(int(habit_id))
        if row is None or model is None:
            return None
        menu = Menu(self)
        menu.add_action("Edit", lambda: self.edit_habit(habit_id), icon="edit", shortcut_text="E")
        skipped = model.state == "skipped"
        menu.add_action("Unskip" if skipped else "Skip today", lambda: self.toggle_skip(habit_id), icon="skip", shortcut_text="S")
        menu.add_separator()
        menu.add_action("Archive", lambda: self.archive(habit_id), icon="archive")
        menu.add_action("Delete…", lambda: self.delete_habit(habit_id), icon="trash", danger=True)
        row.set_actions_pinned(True)
        menu.aboutToHide.connect(lambda: row.set_actions_pinned(False))
        self._menu = menu
        menu.popup_below(row.more, align="right")
        return menu

    def _toggle_not_today(self) -> None:
        self._not_today_open = not self._not_today_open
        self.refresh()
        toggle = getattr(self, "not_today_toggle", None)
        if toggle is not None:
            toggle.setFocus(Qt.FocusReason.OtherFocusReason)

    def _toggle_archived(self) -> None:
        self._archived_open = not self._archived_open
        self._refresh_archived()

    # ================================================================== keyboard
    def _navigate(self, habit_id: int, step: int) -> None:
        order = [h for h in self._order if h in self._rows]
        if not order:
            return
        if habit_id not in order:
            idx = 0
        elif abs(step) >= 999:
            idx = 0 if step < 0 else len(order) - 1
        else:
            idx = max(0, min(len(order) - 1, order.index(habit_id) + step))
        self.focus_row(order[idx])

    def focus_row(self, habit_id: int) -> None:
        """Keyboard-select *habit_id* (scrolls it into view)."""
        row = self._rows.get(int(habit_id))
        if row is None:
            return
        row.setFocus(Qt.FocusReason.OtherFocusReason)
        self.ensureWidgetVisible(row, 0, 24)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        order = [h for h in self._order if h in self._rows]
        if order and key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Home, Qt.Key.Key_End):
            self.focus_row(order[0] if key in (Qt.Key.Key_Down, Qt.Key.Key_Home) else order[-1])
            event.accept()
            return
        super().keyPressEvent(event)


class _SuggestionGrid(QWidget):
    """The six starter habits as centred chip rows (two per row); ``picked(name)`` on click."""

    picked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.chips: list[Chip] = []
        for i in range(0, len(SUGGESTIONS), 2):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addStretch(1)
            for s in SUGGESTIONS[i : i + 2]:
                chip = Chip(f"{s.emoji}  {s.name}", False, None, self)
                chip.setAccessibleName(f"Start with: {s.name}")
                chip.setToolTip("Opens the editor pre-filled")
                chip.clicked.connect(lambda _c=False, name=s.name: self.picked.emit(name))
                row.addWidget(chip)
                self.chips.append(chip)
            row.addStretch(1)
            v.addLayout(row)
        self.setAccessibleName("Suggested habits")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)


def _spacer(height: int) -> QWidget:
    w = QWidget()
    w.setFixedHeight(height)
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return w
