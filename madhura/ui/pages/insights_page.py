"""InsightsPage — "How did my focus go?" (spec §1.9).

Layout (max width 1120)::

    Insights                                   [7 days | 30 days | 12 weeks]  [⤓]
    [Focus time] [Sessions] [Focus score] [Habits]           KPI StatCards (4 across, 2×2 when narrow)
    [Daily focus ─────────────────────────────── bar chart, goal line ─]
    [When you focus best (heatmap) ][Top projects / tasks]   side by side ≥ 960 px window, else stacked
    [What pulls you away — "You noticed and came back N times" + reasons]
    [Recent sessions — last 50 in range, ⋯ → Delete (undo toast)]

Data comes from :func:`madhura.ui.pages._insights_kit.load_snapshot` (logical days via
``ctx.today()``; counting rule: completed + ended early only). The page marks itself dirty on
``data_changed`` / ``day_changed`` / theme changes and refreshes when shown (``on_shown``) or
right away while visible. Range switches cross-fade (160 ms) and KPI numbers count up (400 ms);
both respect reduce-motion. Zero sessions ever → an empty state with "Plan a block".
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEasingCurve, QObject, QRect, QStandardPaths, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from madhura.core import analytics as an
from madhura.core.days import week_start
from madhura.core.models import Outcome, Session
from madhura.core.timefmt import fmt_duration
from madhura.ui import animations, dialogs, fonts, icons, theme
from madhura.ui.pages._insights_kit import (
    RANGE_KEY,
    InsightsSnapshot,
    bar_detail,
    bar_label,
    bar_title,
    fmt_pct,
    fmt_session_when,
    load_snapshot,
    quote,
    sessions_word,
)
from madhura.ui.widgets._common import KeyboardFocusMixin
from madhura.ui.widgets.buttons import Button, IconButton
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.charts import Bar, BarChart, HBarItem, HBarList, Heatmap
from madhura.ui.widgets.chips import SegmentedControl
from madhura.ui.widgets.empty_state import EmptyState, IconMedallion
from madhura.ui.widgets.labels import Badge, Caption, ElidedLabel, Label
from madhura.ui.widgets.list_row import ListRow
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.page import PageHeader, PageScroll
from madhura.ui.widgets.stat_card import StatCard

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["InsightsPage", "RANGE_OPTIONS", "SIDE_BY_SIDE_MIN", "KPI_FOUR_COLUMNS_MIN"]

log = logging.getLogger(__name__)

RANGE_OPTIONS: list[tuple[str, str]] = [("7d", "7 days"), ("30d", "30 days"), ("12w", "12 weeks")]
#: Heatmap and Top projects sit side by side from this window width (else stacked).
SIDE_BY_SIDE_MIN = 960
#: KPI cards go 4 across from this content width (else a 2×2 grid, so hints never truncate).
KPI_FOUR_COLUMNS_MIN = 992
#: Range switch cross-fade.
FADE_MS = 160
#: Undo window for "Delete session".
UNDO_MS = 5000
#: The drift card stacks its two halves below this width.
DRIFT_STACK_BELOW = 560
_DIRTY_KINDS = {"sessions", "checkins", "habits", "settings", "all"}

# Recent-session column widths.
_COL_WHEN = 120
_COL_PROJECT = 124
_COL_DURATION = 56
_COL_SCORE = 44
_COL_NOTE = 18


# =========================================================================== small widgets


class _FadeOverlay(QWidget):
    """Paints a snapshot of the old content on top and fades it out (a true cross-fade without
    putting a QGraphicsEffect on text widgets); deletes itself when done."""

    def __init__(self, target: QWidget, pixmap: QPixmap, rect: QRect, ms: int) -> None:
        super().__init__(target)
        self._pixmap = pixmap
        self._opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setGeometry(rect)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(int(ms))
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()

    def start(self) -> None:
        """Begin fading out (call after the new content is in place, so no frames are lost)."""
        self._anim.start()

    def _on_value(self, value: object) -> None:
        self._opacity = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.drawPixmap(0, 0, self._pixmap)
        p.end()


@dataclass(eq=False)
class _PendingDelete:
    session_id: int
    token: Any = None
    timer: QTimer | None = None


class _UndoDeletes(QObject):
    """Sessions deleted from the list: hidden at once, deleted from the database when the "Undo"
    toast expires (timeout, replaced, or flushed on quit) — or restored when Undo is clicked."""

    def __init__(self, ctx: AppContext, on_change: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._on_change = on_change
        self._pending: dict[int, _PendingDelete] = {}
        self._host: Any = None

    def ids(self) -> set[int]:
        """Session ids waiting for their toast."""
        return set(self._pending)

    def push(self, session_id: int, text: str) -> None:
        """Hide *session_id* and show the undo toast."""
        window = self._ctx.window
        if window is None:
            self._delete(session_id)
            return
        entry = _PendingDelete(session_id)
        self._pending[session_id] = entry
        self._on_change()
        token = window.show_toast(text, "info", "Undo", lambda sid=session_id: self.undo(sid), UNDO_MS)
        host = getattr(window, "toasts", None)
        expired = getattr(host, "expired", None)
        if isinstance(token, int) and not isinstance(token, bool) and expired is not None:
            entry.token = token
            if self._host is not host:
                expired.connect(self._on_expired)
                self._host = host
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(UNDO_MS + 250)
        timer.timeout.connect(lambda sid=session_id: self.commit(sid))
        entry.timer = timer
        timer.start()

    def _take(self, session_id: int) -> bool:
        entry = self._pending.pop(session_id, None)
        if entry is None:
            return False
        if entry.timer is not None:
            entry.timer.stop()
            entry.timer.deleteLater()
        return True

    def undo(self, session_id: int) -> None:
        """Restore a pending session."""
        if self._take(session_id):
            self._on_change()

    def commit(self, session_id: int) -> None:
        """Delete a pending session from the database now."""
        if self._take(session_id):
            self._delete(session_id)

    def flush(self) -> None:
        """Commit every pending delete (quit)."""
        for sid in list(self._pending):
            self.commit(sid)

    def _on_expired(self, token: int) -> None:
        for sid, entry in list(self._pending.items()):
            if entry.token == token:
                self.commit(sid)

    def _delete(self, session_id: int) -> None:
        repo = self._ctx.repo
        if getattr(repo, "closed", False):
            log.warning("Repository closed; dropping a pending session delete")
            return
        try:
            repo.delete_session(session_id)
        except sqlite3.Error:
            log.exception("Could not delete session %s", session_id)
            return
        self._ctx.events.data_changed.emit("sessions")


# =========================================================================== KPI grid


class _KpiGrid(QWidget):
    """Four StatCards: one row when wide, 2×2 below :data:`KPI_FOUR_COLUMNS_MIN`."""

    def __init__(self, cards: list[StatCard], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards = cards
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._relayout(4)

    def columns(self) -> int:
        """Current number of columns (4 or 2)."""
        return self._columns

    def _relayout(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for card in self._cards:
            self._grid.removeWidget(card)
        for i in range(4):
            self._grid.setColumnStretch(i, 0)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // columns, i % columns)
        for i in range(columns):
            self._grid.setColumnStretch(i, 1)
        rows = (len(self._cards) + columns - 1) // columns
        self.setFixedHeight(rows * 108 + (rows - 1) * 16)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout(4 if event.size().width() >= KPI_FOUR_COLUMNS_MIN else 2)


# =========================================================================== drift panel


class _DriftPanel(QWidget):
    """'What pulls you away' body: the came-back count on the left, reasons on the right."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(32)
        left = QWidget(self)
        left.setFixedWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 2, 0, 0)
        ll.setSpacing(0)
        self.medallion = IconMedallion("repeat", 40, left)
        ll.addWidget(self.medallion, 0, Qt.AlignmentFlag.AlignLeft)
        ll.addSpacing(12)
        self.headline = Label("", "label", left)
        self.headline.setWordWrap(True)
        ll.addWidget(self.headline)
        ll.addSpacing(4)
        self.note = Caption("", left)
        self.note.setWordWrap(True)
        ll.addWidget(self.note)
        ll.addStretch(1)
        self.left = left
        self.reasons = HBarList(self)
        self.reasons.setAccessibleName("Drift reasons")
        self._layout.addWidget(left, 0)
        self._layout.addWidget(self.reasons, 1, Qt.AlignmentFlag.AlignTop)

    def is_stacked(self) -> bool:
        """True when the two halves are stacked vertically (narrow cards)."""
        return self._layout.direction() == QBoxLayout.Direction.TopToBottom

    def set_stacked(self, stacked: bool) -> None:
        """Stack the two halves vertically on narrow cards."""
        if stacked == self.is_stacked():
            return
        self._layout.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self._layout.setSpacing(20 if stacked else 32)
        self._apply_left_width()

    def _apply_left_width(self) -> None:
        if self.is_stacked() or self.reasons.isHidden():
            self.left.setMinimumWidth(0)
            self.left.setMaximumWidth(16777215)
        else:
            self.left.setFixedWidth(240)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.set_stacked(event.size().width() < DRIFT_STACK_BELOW)

    def set_data(self, came_back: int, answered: int, reasons: list[tuple[str, int]], accent: QColor) -> None:
        """Fill from the snapshot numbers."""
        if answered <= 0:
            self.medallion.set_icon("bell")
            self.headline.setText("No check-ins in this range")
            self.note.setText("Answer a few check-ins and you'll see what tends to pull you away.")
            self.reasons.hide()
            self._apply_left_width()
            return
        if came_back <= 0:
            self.medallion.set_icon("check_circle")
            self.headline.setText("Nothing pulled you away")
            self.note.setText("Every check-in in this range found you focused.")
            self.reasons.hide()
            self._apply_left_width()
            return
        self.medallion.set_icon("repeat")
        times = "once" if came_back == 1 else f"{came_back} times"
        self.headline.setText(f"You noticed and came back {times}")
        self.note.setText("Drifting is normal — noticing is the skill. Every return counts.")
        self.reasons.show()
        self._apply_left_width()
        total = sum(n for _r, n in reasons)
        items = [
            HBarItem(reason, n, f"{n}×", accent, f"{reason} · {n} of {total} noted drifts")
            for reason, n in reasons
        ]
        self.reasons.set_items(items)
        self.reasons.set_empty_text("No reasons noted yet — tap one after “Focusing back” next time.")


# =========================================================================== recent sessions


def _elide(text: str, font_px: int, weight: int, width: int) -> str:
    return QFontMetrics(fonts.font(font_px, weight, True)).elidedText(text, Qt.TextElideMode.ElideRight, width)


def _fixed(widget: QWidget, width: int) -> QWidget:
    widget.setFixedWidth(width)
    return widget


class _RecentRow(ListRow):
    """One recent session: when · task · project · duration · score · note · outcome · ⋯."""

    def __init__(
        self,
        session: Session,
        counts: dict[str, int],
        when: str,
        on_delete: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(44, parent, separator=True, clickable=False, padding=(12, 8))
        self.session = session
        self._on_delete = on_delete
        self._layout.setSpacing(12)
        self.trailing_layout.setSpacing(12)
        abandoned = session.outcome == Outcome.ABANDONED

        self.when_label = QLabel(when, self)
        self.when_label.setFont(fonts.font(12, 400, tabular=True))
        self.when_label.setProperty("tone", "muted")
        self.add_leading(_fixed(self.when_label, _COL_WHEN))

        title_row = QWidget(self)
        tl = QHBoxLayout(title_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        self.title = ElidedLabel(session.task, "label", title_row)
        if abandoned:
            self.title.setProperty("tone", "muted")
        tl.addWidget(self.title, 1)
        self.outcome_badge: Badge | None = None
        if session.outcome == Outcome.ENDED_EARLY:
            self.outcome_badge = Badge("Ended early", "neutral", title_row)
        elif abandoned:
            self.outcome_badge = Badge("Abandoned", "neutral", title_row)
            self.outcome_badge.setToolTip("Not counted in your totals")
        if self.outcome_badge is not None:
            tl.addWidget(self.outcome_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.add_content(title_row)

        project_box = _fixed(QWidget(self), _COL_PROJECT)
        pl = QHBoxLayout(project_box)
        pl.setContentsMargins(0, 0, 0, 0)
        self.project_badge: Badge | None = None
        if session.project:
            text = _elide(session.project, 11, 600, _COL_PROJECT - 30)
            self.project_badge = Badge(text, "neutral", project_box, dot=theme.project_color(session.project))
            if text != session.project:
                self.project_badge.setToolTip(session.project)
            pl.addWidget(self.project_badge, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        pl.addStretch(1)
        self.add_trailing(project_box)

        self.duration_label = QLabel(fmt_duration(session.focused_seconds), self)
        self.duration_label.setFont(fonts.font(13, 500, tabular=True))
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if abandoned:
            self.duration_label.setProperty("tone", "muted")
            self.duration_label.setToolTip("Not counted in your totals")
        self.add_trailing(_fixed(self.duration_label, _COL_DURATION))

        answered = counts.get("focused", 0) + counts.get("drifted", 0)
        score = counts.get("focused", 0) / answered if answered else None
        self.score_label = QLabel(fmt_pct(score) if score is not None else "", self)
        self.score_label.setFont(fonts.font(12, 500, tabular=True))
        self.score_label.setProperty("tone", "muted")
        self.score_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if score is not None:
            tip = f"{counts.get('focused', 0)} of {answered} check-ins focused"
            if counts.get("missed", 0):
                tip += f" · {counts['missed']} unanswered"
            self.score_label.setToolTip(tip)
        self.add_trailing(_fixed(self.score_label, _COL_SCORE))

        self.note_icon = QLabel(self)
        self.note_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if session.note:
            self.note_icon.setPixmap(icons.pixmap("note", 16, theme.palette().text_muted, self.devicePixelRatioF()))
            self.note_icon.setToolTip(session.note)
            self.note_icon.setAccessibleName("Note")
            self.note_icon.setAccessibleDescription(session.note)
        self.add_trailing(_fixed(self.note_icon, _COL_NOTE))

        self.more_button = IconButton("more", "More actions", 28, self, accessible_name=f"More actions for {quote(session.task)}")
        self.more_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_button.clicked.connect(self.open_menu)
        self.add_trailing(self.more_button, hover_only=True)
        self.setAccessibleName(f"{when}, {session.task}, {fmt_duration(session.focused_seconds)}")

    def open_menu(self) -> Menu:
        """Show the ⋯ menu (Delete session)."""
        menu = Menu(self)
        menu.add_action("Delete session", lambda: self._on_delete(self.session.id), icon="trash", danger=True)
        self.set_actions_pinned(True)
        menu.aboutToHide.connect(lambda: self.set_actions_pinned(False))
        menu.popup_below(self.more_button, align="right")
        return menu


class _RecentList(KeyboardFocusMixin, QWidget):
    """Rows + keyboard: ↑/↓ select, Delete deletes, Enter / Menu key opens ⋯."""

    def __init__(self, on_delete: Callable[[int], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_delete = on_delete
        self.rows: list[_RecentRow] = []
        self._selected = -1
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.empty_label = Caption("No sessions in this range.", self)
        self.empty_label.setContentsMargins(12, 8, 0, 8)
        self._layout.addWidget(self.empty_label)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Recent sessions")
        self._ring_radius = 8.0

    def set_sessions(self, rows: list[tuple[Session, dict[str, int], str]]) -> None:
        """Rebuild the rows (session, check-in counts, "when" text)."""
        keep = self.rows[self._selected].session.id if 0 <= self._selected < len(self.rows) else None
        for row in self.rows:
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        for session, counts, when in rows:
            row = _RecentRow(session, counts, when, self._on_delete, self)
            self._layout.addWidget(row)
            self.rows.append(row)
        if self.rows:
            self.rows[-1].set_separator(False)
        self.empty_label.setVisible(not self.rows)
        ids = [r.session.id for r in self.rows]
        self._selected = -1
        if keep is not None and self.hasFocus():
            self.select(ids.index(keep) if keep in ids else min(len(ids) - 1, 0))

    def selected_index(self) -> int:
        """Keyboard-selected row (-1 = none)."""
        return self._selected

    def select(self, index: int) -> None:
        """Select row *index* (and scroll it into view)."""
        if not self.rows:
            self._selected = -1
            return
        index = max(0, min(len(self.rows) - 1, index))
        for i, row in enumerate(self.rows):
            row.set_selected(i == index)
        self._selected = index
        scroll = self._scroll_area()
        if scroll is not None:
            scroll.ensureWidgetVisible(self.rows[index], 0, 24)

    def _scroll_area(self) -> Any:
        w = self.parentWidget()
        while w is not None:
            if hasattr(w, "ensureWidgetVisible"):
                return w
            w = w.parentWidget()
        return None

    def focusInEvent(self, event: Any) -> None:  # noqa: N802
        super().focusInEvent(event)
        if self.rows and self._selected < 0:
            self.select(0)

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        super().focusOutEvent(event)
        for row in self.rows:
            row.set_selected(False)
        self._selected = -1

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if not self.rows:
            super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Home, Qt.Key.Key_End):
            cur = self._selected
            if key == Qt.Key.Key_Down:
                cur = cur + 1 if cur >= 0 else 0
            elif key == Qt.Key.Key_Up:
                cur = cur - 1 if cur > 0 else 0
            elif key == Qt.Key.Key_Home:
                cur = 0
            else:
                cur = len(self.rows) - 1
            self.select(cur)
            event.accept()
            return
        if 0 <= self._selected < len(self.rows):
            row = self.rows[self._selected]
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._on_delete(row.session.id)
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Menu):
                row.open_menu()
                event.accept()
                return
        super().keyPressEvent(event)


# =========================================================================== page


class InsightsPage(PageScroll):
    """Focus analytics page (spec §1.9)."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(1120, parent, spacing=0)
        self._ctx = ctx
        self._dirty = True
        self._refresh_queued = False
        self._snapshot: InsightsSnapshot | None = None
        self._shown_once = False
        self._top_mode: str | None = None
        self._wide: bool | None = None
        self.undo = _UndoDeletes(ctx, self._on_pending_changed, self)
        col = self.column

        # Header.
        stored = None
        try:
            stored = ctx.repo.kv_get(RANGE_KEY)
        except sqlite3.Error:
            log.debug("Could not read the insights range", exc_info=True)
        self._range = stored if stored in an.RANGES else "7d"
        self.range_control = SegmentedControl(RANGE_OPTIONS, self._range, col)
        self.range_control.setAccessibleName("Range")
        self.range_control.valueChanged.connect(self._on_range_changed)
        self.export_button = IconButton("download", "Export", 32, col, variant="secondary", accessible_name="Export")
        self.export_button.clicked.connect(self.open_export_menu)
        self.header = PageHeader("Insights", [self.range_control, self.export_button], col)
        self.content_layout.addWidget(self.header)
        self.content_layout.addSpacing(24)

        # Empty state (zero sessions ever).
        plan = Button("Plan a block", "primary", icon="play", parent=col)
        plan.clicked.connect(self._plan_a_block)
        self.plan_button = plan
        self.empty = EmptyState(
            "chart",
            "Your focus story starts here",
            "Finish a focus block and your totals, streaks and patterns appear here.",
            plan,
            col,
        )
        self.empty_card = Card(padding=24, parent=col)
        self.empty_card.body.addWidget(self.empty)
        self.content_layout.addWidget(self.empty_card)

        # Body.
        self.body = QWidget(col)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self.kpi_focus = StatCard("Focus time", self.body)
        self.kpi_sessions = StatCard("Sessions", self.body)
        self.kpi_score = StatCard("Focus score", self.body)
        self.kpi_habits = StatCard("Habits", self.body)
        self.kpis = [self.kpi_focus, self.kpi_sessions, self.kpi_score, self.kpi_habits]
        self.kpi_grid = _KpiGrid(self.kpis, self.body)
        body.addWidget(self.kpi_grid)

        self.chart_average = Caption("", self.body)
        self.chart_card = Card("Daily focus", action=self.chart_average, padding=(20, 18, 20, 14), parent=self.body)
        self.chart_card.setFixedHeight(260)
        self.chart = BarChart(self.chart_card)
        self.chart_card.body.addWidget(self.chart, 1)
        body.addWidget(self.chart_card)

        self.row = QWidget(self.body)
        self._row_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.row)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(16)
        self.heatmap_card = Card("When you focus best", parent=self.row)
        self.heatmap = Heatmap(self.heatmap_card)
        self.heatmap_card.body.addWidget(self.heatmap)
        self.heatmap_card.body.addStretch(1)
        self.top_toggle = SegmentedControl([("projects", "Projects"), ("tasks", "Tasks")], "projects", self.row, size="sm")
        self.top_toggle.setAccessibleName("Rank by")
        self.top_toggle.valueChanged.connect(self._on_top_mode)
        self.top_card = Card("Top projects", action=self.top_toggle, parent=self.row)
        self.top_list = HBarList(self.top_card)
        self.top_list.setAccessibleName("Top projects")
        self.top_card.body.addWidget(self.top_list)
        self.top_card.body.addStretch(1)
        self._row_layout.addWidget(self.heatmap_card, 3)
        self._row_layout.addWidget(self.top_card, 2)
        body.addWidget(self.row)

        self.drift_card = Card("What pulls you away", parent=self.body)
        self.drift = _DriftPanel(self.drift_card)
        self.drift_card.body.addWidget(self.drift)
        body.addWidget(self.drift_card)

        self.recent_count = Caption("", self.body)
        self.recent_card = Card("Recent sessions", action=self.recent_count, padding=(8, 18, 8, 10), parent=self.body)
        self.recent_card.header_layout.setContentsMargins(12, 0, 12, 0)
        self.recent_list = _RecentList(self.delete_session, self.recent_card)
        self.recent_card.body.addWidget(self.recent_list)
        body.addWidget(self.recent_card)

        self.content_layout.addWidget(self.body)
        self.content_layout.addStretch(1)
        self._show_empty(False)

        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.events.day_changed.connect(self._on_day_changed)
        ctx.theme.changed.connect(self._on_theme_changed)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.undo.flush)

    # ------------------------------------------------------------------ public API
    def on_shown(self) -> None:
        """Refresh when shown (only if something changed since the last refresh)."""
        first = not self._shown_once
        self._shown_once = True
        if self._dirty or self._snapshot is None or self._snapshot.today != self._ctx.today():
            self.refresh(animate=first or self._snapshot is None or self._dirty)

    def current_range(self) -> str:
        """Selected range key ("7d" | "30d" | "12w")."""
        return self._range

    def set_range(self, key: str, *, animate: bool = True) -> None:
        """Switch the range (cross-fade + count-up), remembering it in kv."""
        if key not in an.RANGES:
            return
        self.range_control.set_value(key)
        if key == self._range and self._snapshot is not None:
            return
        self._range = key
        try:
            self._ctx.repo.kv_set(RANGE_KEY, key)
        except sqlite3.Error:
            log.debug("Could not store the insights range", exc_info=True)
        overlay = self._cross_fade() if animate else None
        self.refresh(animate=animate)
        if overlay is not None:
            overlay.start()

    def snapshot(self) -> InsightsSnapshot | None:
        """The data shown right now."""
        return self._snapshot

    def is_empty_state(self) -> bool:
        """True while the "starts here" empty state is shown."""
        return not self.empty_card.isHidden()

    def is_side_by_side(self) -> bool:
        """True when heatmap and top projects share a row."""
        return self._row_layout.direction() == QBoxLayout.Direction.LeftToRight

    def top_mode(self) -> str:
        """"projects" or "tasks" (what the ranked list shows)."""
        return self.top_toggle.value() or "projects"

    def refresh(self, animate: bool = False) -> None:
        """Reload everything from the database."""
        self._refresh_queued = False
        self._dirty = False
        try:
            snap = load_snapshot(self._ctx, self._range, self.undo.ids())
        except sqlite3.Error:
            log.exception("Could not load insights")
            return
        self._snapshot = snap
        self._update_wide()
        if not snap.any_sessions:
            self._show_empty(True)
            return
        self._show_empty(False)
        motion = animate and animations.motion_enabled() and self.isVisible()
        self._fill_kpis(snap, motion)
        self._fill_chart(snap, motion)
        self._fill_heatmap(snap)
        self._fill_top(snap)
        self._fill_drift(snap)
        self._fill_recent(snap)

    # ------------------------------------------------------------------ export
    def open_export_menu(self) -> Menu:
        """Show the export menu under the download button."""
        menu = Menu(self)
        menu.add_action("Export sessions (CSV)…", self.export_csv, icon="download")
        menu.popup_below(self.export_button, align="right")
        return menu

    def default_export_name(self) -> str:
        """Suggested file name for the selected range."""
        snap = self._snapshot
        today = self._ctx.today()
        period = snap.period if snap is not None else an.range_period(self._range, today)
        return f"madhura-sessions-{period.start.isoformat()}-to-{period.end.isoformat()}.csv"

    def export_csv(self) -> bool:
        """Ask for a path and export the selected range's sessions. True when a file was written."""
        from madhura.data.export import export_sessions_csv

        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        suggested = os.path.join(docs, self.default_export_name())
        path = dialogs.get_save_path(self, "Export sessions", suggested, "CSV files (*.csv)")
        if not path:
            return False
        if not path.lower().endswith(".csv"):
            path += ".csv"
        today = self._ctx.today()
        period = an.range_period(self._range, today)
        start, end = an.period_bounds(period, self._ctx.day_start_hour())
        try:
            count = export_sessions_csv(path, self._ctx.repo, start, end, day_start_hour=self._ctx.day_start_hour())
        except (OSError, sqlite3.Error) as exc:
            log.exception("CSV export failed")
            dialogs.show_error(self, "Couldn't export sessions", str(exc))
            return False
        if self._ctx.window is not None:
            self._ctx.window.show_toast(f"Exported {sessions_word(count)} to {os.path.basename(path)}", "success")
        return True

    # ------------------------------------------------------------------ delete
    def delete_session(self, session_id: int) -> None:
        """Hide a session now; it is deleted from the database when the Undo toast goes."""
        if session_id in self.undo.ids():
            return
        session = None
        try:
            session = self._ctx.repo.get_session(session_id)
        except sqlite3.Error:
            log.exception("Could not read session %s", session_id)
        if session is None:
            return
        self.undo.push(session_id, f"Deleted {quote(session.task, 32)}")

    def _on_pending_changed(self) -> None:
        self.refresh(animate=False)

    # ------------------------------------------------------------------ fill sections
    def _vs_caption(self) -> str:
        return f"vs previous {an.RANGE_LABELS[self._range]}"

    def _fill_kpis(self, snap: InsightsSnapshot, animate: bool) -> None:
        vs = self._vs_caption()
        self.kpi_focus.set_number(snap.focus_seconds, fmt_duration, animate=animate)
        self.kpi_focus.set_delta(snap.focus_delta, vs)
        current, best = snap.streak
        if current > 0:
            self.kpi_focus.set_hint(f"Streak {current} · best {best}")
        elif best > 0:
            self.kpi_focus.set_hint(f"Best streak {best}")
        else:
            self.kpi_focus.set_hint("")

        self.kpi_sessions.set_number(snap.session_count, lambda v: f"{int(round(v)):,}", animate=animate)
        self.kpi_sessions.set_delta(snap.sessions_delta, vs)
        self.kpi_sessions.set_hint("")

        if snap.score is None:
            self.kpi_score.set_number(None)
        else:
            self.kpi_score.set_number(snap.score * 100, lambda v: f"{int(round(v))}%", animate=animate)
        self.kpi_score.set_delta(snap.score_delta, vs, unit="pts")
        if snap.came_back > 0:
            self.kpi_score.set_hint("Came back once" if snap.came_back == 1 else f"Came back {snap.came_back} times")
        else:
            self.kpi_score.set_hint("No drifts" if snap.answered else "")

        if snap.habit_rate is None:
            self.kpi_habits.set_number(None)
            self.kpi_habits.set_hint("")
        else:
            self.kpi_habits.set_number(snap.habit_rate * 100, lambda v: f"{int(round(v))}%", animate=animate)
            self.kpi_habits.set_hint(f"{snap.habit_done} of {snap.habit_scheduled} done")
        self.kpi_habits.set_delta(snap.habit_delta, vs, unit="pts")

    def _fill_chart(self, snap: InsightsSnapshot, animate: bool) -> None:
        rk = snap.range_key
        today = snap.today
        this_week = week_start(today)
        n = len(snap.buckets)
        bars = []
        for i, st in enumerate(snap.buckets):
            current = st.day == (this_week if rk == "12w" else today)
            label = bar_label(rk, st.day, i, n)
            bars.append(Bar(st.seconds, label, bar_title(rk, st.day), bar_detail(st.seconds, st.sessions), current))
        goal = snap.goal_seconds
        self.chart.set_data(bars, goal or None, f"Goal {fmt_duration(goal)}" if goal else "", animate=animate)
        weekly = rk == "12w"
        self.chart_card.set_title("Weekly focus" if weekly else "Daily focus")
        total = snap.focus_seconds
        if weekly:
            avg = total / max(1.0, snap.period.days / 7.0)
            self.chart_average.setText(f"Avg {fmt_duration(avg)} a week")
        else:
            self.chart_average.setText(f"Avg {fmt_duration(total / max(1, snap.period.days))} a day")
        best = max(snap.buckets, key=lambda s: s.seconds, default=None)
        unit = "week" if weekly else "day"
        parts = [f"{'Weekly' if weekly else 'Daily'} focus over the last {an.RANGE_LABELS[rk]}: {fmt_duration(total)} in total."]
        if best is not None and best.seconds > 0:
            parts.append(f"Best {unit}: {bar_title(rk, best.day)} with {fmt_duration(best.seconds)}.")
        if goal:
            met = sum(1 for s in snap.buckets if s.seconds >= goal)
            parts.append(f"Goal {fmt_duration(goal)} met in {met} of {n} {unit}s.")
        self.chart.setAccessibleDescription(" ".join(parts))

    def _fill_heatmap(self, snap: InsightsSnapshot) -> None:
        self.heatmap.set_data(snap.matrix, enough=snap.enough_for_heatmap, use_24h=self._ctx.use_24h())

    def _fill_top(self, snap: InsightsSnapshot) -> None:
        if self._top_mode is None:
            has_projects = any(r.label is not None for r in snap.projects)
            self.top_toggle.set_value("projects" if has_projects or not snap.tasks else "tasks")
        mode = self.top_mode()
        items_src = snap.projects if mode == "projects" else snap.tasks
        total = max(1, snap.focus_seconds)
        items = []
        for r in items_src:
            label = r.label if r.label is not None else "No project"
            share = int(round(100 * r.seconds / total))
            tip = f"{label} · {fmt_duration(r.seconds)} · {sessions_word(r.sessions)} · {share}% of your focus"
            items.append(HBarItem(label, r.seconds, fmt_duration(r.seconds), theme.project_color(r.project), tip))
        self.top_card.set_title("Top projects" if mode == "projects" else "Top tasks")
        self.top_list.setAccessibleName("Top projects" if mode == "projects" else "Top tasks")
        self.top_list.set_empty_text("No focus time in this range yet.")
        self.top_list.set_items(items)

    def _fill_drift(self, snap: InsightsSnapshot) -> None:
        self.drift.set_data(snap.came_back, snap.answered, snap.drift, theme.with_alpha(theme.palette().accent, 0.55))

    def _fill_recent(self, snap: InsightsSnapshot) -> None:
        hour = self._ctx.day_start_hour()
        use_24h = self._ctx.use_24h()
        rows = [
            (s, snap.recent_counts.get(s.id, {}), fmt_session_when(s.started_at, snap.today, hour, use_24h))
            for s in snap.recent
        ]
        self.recent_list.set_sessions(rows)
        n = len(snap.recent)
        if n >= 50:
            self.recent_count.setText("Last 50 in this range")
        else:
            self.recent_count.setText(sessions_word(n) if n else "")

    # ------------------------------------------------------------------ layout
    def _show_empty(self, empty: bool) -> None:
        self.empty_card.setVisible(empty)
        self.body.setVisible(not empty)
        self.range_control.setVisible(not empty)
        self.export_button.setVisible(not empty)

    def _update_wide(self) -> None:
        wide = self.window().width() >= SIDE_BY_SIDE_MIN
        if wide == self._wide:
            return
        self._wide = wide
        self._row_layout.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        self._row_layout.setStretch(0, 3 if wide else 0)
        self._row_layout.setStretch(1, 2 if wide else 0)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_wide()

    def _cross_fade(self) -> _FadeOverlay | None:
        """Cover the visible part of the body with a snapshot of the old content (not started)."""
        if not animations.motion_enabled() or not self.body.isVisible() or self.body.width() <= 0:
            return None
        for old in self.body.findChildren(_FadeOverlay):
            old.hide()
            old.deleteLater()
        rect = self.body.visibleRegion().boundingRect()  # only what is on screen
        if rect.isEmpty():
            return None
        return _FadeOverlay(self.body, self.body.grab(rect), rect, FADE_MS)

    # ------------------------------------------------------------------ reactions
    def _on_range_changed(self, value: object) -> None:
        self.set_range(str(value), animate=True)

    def _on_top_mode(self, value: object) -> None:
        self._top_mode = str(value)
        self.top_toggle.set_value(self._top_mode)
        if self._snapshot is not None:
            self._fill_top(self._snapshot)

    def _plan_a_block(self) -> None:
        self._ctx.events.navigate.emit("focus")
        if self._ctx.window is not None:
            self._ctx.window.focus_task_input()

    def _schedule_refresh(self) -> None:
        if self._refresh_queued:
            return
        self._refresh_queued = True
        QTimer.singleShot(0, self, self._queued_refresh)

    def _queued_refresh(self) -> None:
        if not self._refresh_queued:
            return
        if self.isVisible():
            self.refresh(animate=True)
        else:
            self._refresh_queued = False

    def _on_data_changed(self, kind: str) -> None:
        if kind not in _DIRTY_KINDS:
            return
        self._dirty = True
        if self.isVisible():
            self._schedule_refresh()

    def _on_day_changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._schedule_refresh()

    def _on_theme_changed(self) -> None:
        if self._snapshot is None:
            return
        if self.isVisible():
            self._fill_top(self._snapshot)
            self._fill_drift(self._snapshot)
            self._fill_recent(self._snapshot)
        else:
            self._dirty = True
