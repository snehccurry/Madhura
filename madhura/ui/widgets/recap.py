"""WeeklyRecapCard — last week at a glance, shown on the Focus page (spec §1.9).

"Last week: 11h 20m (+18%) · best day Thu · top project Madhura · 84% focused" with "See insights"
and a dismiss ×. It shows on the first open in a new ISO week (kv ``weekly_recap_seen`` = the week
it was last shown or dismissed, e.g. ``"2026-W42"``) and only when last week had counted focus.
The Focus page [P1] embeds it: it calls :meth:`refresh` and :meth:`should_show`, and hides the
card on :attr:`dismissed`.
"""

from __future__ import annotations

import html
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from madhura.core import analytics as an
from madhura.core.days import week_start
from madhura.core.timefmt import WEEKDAYS_SHORT, fmt_duration
from madhura.ui import fonts, icons, theme
from madhura.ui.widgets.buttons import IconButton, LinkButton
from madhura.ui.widgets.card import Card

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["WeeklyRecapCard", "RecapData", "compute_recap", "recap_text", "week_key", "SEEN_KEY"]

log = logging.getLogger(__name__)

#: kv key: ISO week ("2026-W42") in which the recap was last shown or dismissed.
SEEN_KEY = "weekly_recap_seen"


def week_key(d: date) -> str:
    """ISO week label of *d*: ``"2026-W42"``."""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


@dataclass(frozen=True)
class RecapData:
    """Last week's numbers."""

    week_start: date
    seconds: int
    delta: float | None
    best_day: date | None
    top_project: str | None
    score: float | None


def compute_recap(ctx: AppContext) -> RecapData | None:
    """Numbers for the ISO week before ``ctx.today()``'s week (None when it had no counted focus)."""
    hour = ctx.day_start_hour()
    last = week_start(ctx.today()) - timedelta(days=7)
    period = an.Period(last, last + timedelta(days=6))
    prev = an.previous_period(period)
    start, end = an.period_bounds(period, hour)
    pstart, pend = an.period_bounds(prev, hour)
    sessions = ctx.repo.sessions_between(start, end)
    seconds = an.total_seconds(sessions)
    if seconds <= 0:
        return None
    prev_seconds = an.total_seconds(ctx.repo.sessions_between(pstart, pend))
    ids = {s.id for s in sessions}
    checkins = [c for c in ctx.repo.checkins_between(start, end + timedelta(days=1)) if c.session_id in ids]
    projects = [r for r in an.ranked(sessions, "project", 6) if r.label is not None]
    return RecapData(
        week_start=last,
        seconds=seconds,
        delta=an.period_delta(seconds, prev_seconds),
        best_day=an.best_day(an.daily_totals(sessions, period.start, period.end, hour)),
        top_project=projects[0].label if projects else None,
        score=an.focus_score(checkins),
    )


def _delta_str(delta: float) -> str:
    pct = int(round(delta * 100))
    return f"+{pct}%" if pct >= 0 else f"−{abs(pct)}%"


def recap_text(data: RecapData) -> str:
    """Plain text: ``"Last week: 11h 20m (+18%) · best day Thu · top project X · 84% focused"``."""
    head = fmt_duration(data.seconds)
    if data.delta is not None:
        head += f" ({_delta_str(data.delta)})"
    parts = [head]
    if data.best_day is not None:
        parts.append(f"best day {WEEKDAYS_SHORT[data.best_day.weekday()]}")
    if data.top_project:
        parts.append(f"top project {data.top_project}")
    if data.score is not None:
        parts.append(f"{int(round(data.score * 100))}% focused")
    return "Last week: " + " · ".join(parts)


class WeeklyRecapCard(Card):
    """Dismissible "Last week: …" card for the Focus page."""

    dismissed = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(padding=(42, 8, 8, 8), parent=parent, radius=10.0, background="surface_alt", border="border_strong")
        self._ctx = ctx
        self._data: RecapData | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setFont(fonts.font(13, 400))
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.see_button = LinkButton("See insights", self, tone="accent", weight=600)
        self.see_button.clicked.connect(self.open_insights)
        self.close_button = IconButton("x", "Dismiss", 28, self, accessible_name="Dismiss weekly recap")
        self.close_button.clicked.connect(self.dismiss)
        row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.see_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(row)
        self.setAccessibleName("Weekly recap")
        ctx.theme.changed.connect(self._render)
        ctx.events.day_changed.connect(self._on_day_changed)
        ctx.events.data_changed.connect(self._on_data_changed)
        self.refresh()

    # ------------------------------------------------------------------ data
    def week_key(self) -> str:
        """ISO week of the logical today."""
        return week_key(self._ctx.today())

    def data(self) -> RecapData | None:
        """Last week's numbers (None = nothing to recap)."""
        return self._data

    def refresh(self) -> None:
        """Recompute last week's numbers and the text."""
        try:
            self._data = compute_recap(self._ctx)
        except sqlite3.Error:
            log.exception("Could not compute the weekly recap")
            self._data = None
        self._render()

    def text(self) -> str:
        """The recap as plain text ("" when there is nothing to recap)."""
        return recap_text(self._data) if self._data is not None else ""

    def should_show(self) -> bool:
        """True on the first open in a new ISO week, when last week had counted focus."""
        try:
            seen = self._ctx.repo.kv_get(SEEN_KEY)
        except sqlite3.Error:
            return False
        if seen == self.week_key():
            return False
        if self._data is None or self._data.week_start != week_start(self._ctx.today()) - timedelta(days=7):
            self.refresh()
        return self._data is not None

    # ------------------------------------------------------------------ actions
    def mark_seen(self) -> None:
        """Remember that this week's recap was shown."""
        try:
            self._ctx.repo.kv_set(SEEN_KEY, self.week_key())
        except sqlite3.Error:
            log.debug("Could not store weekly_recap_seen", exc_info=True)

    def dismiss(self) -> None:
        """Hide for the rest of the week and emit :attr:`dismissed`."""
        self.mark_seen()
        self.hide()
        self.dismissed.emit()

    def open_insights(self) -> None:
        """Go to the Insights page (the card stays until dismissed or next week)."""
        self._ctx.events.navigate.emit("insights")

    def _on_day_changed(self) -> None:
        self.refresh()
        if self.isVisible() and self._data is not None:
            self.mark_seen()

    def _on_data_changed(self, kind: str) -> None:
        if kind in ("sessions", "checkins", "all") and self.isVisible():
            self.refresh()

    # ------------------------------------------------------------------ rendering
    def _render(self) -> None:
        data = self._data
        if data is None:
            self.label.setText("")
            self.setAccessibleDescription("")
            return
        pal = theme.palette()
        esc = html.escape
        head = esc(fmt_duration(data.seconds))
        if data.delta is not None:
            tone = pal.success if data.delta > 0 else pal.text_muted
            head += f' <span style="color:{tone};">({esc(_delta_str(data.delta))})</span>'
        parts = [f'<span style="color:{pal.text}; font-weight:600;">{head}</span>']
        muted = pal.text_muted
        if data.best_day is not None:
            parts.append(f'best day <span style="color:{pal.text};">{WEEKDAYS_SHORT[data.best_day.weekday()]}</span>')
        if data.top_project:
            parts.append(f'top project <span style="color:{pal.text};">{esc(data.top_project)}</span>')
        if data.score is not None:
            parts.append(f'<span style="color:{pal.text};">{int(round(data.score * 100))}%</span> focused')
        sep = f' <span style="color:{pal.text_subtle};">·</span> '
        self.label.setText(
            f'<span style="color:{muted};"><span style="color:{pal.text}; font-weight:600;">Last week:</span> '
            + sep.join(parts)
            + "</span>"
        )
        self.setAccessibleDescription(recap_text(data))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        pal = theme.palette()
        p = QPainter(self)
        pm = icons.pixmap("chart", 18, pal.accent, self.devicePixelRatioF())
        lg = self.label.geometry()
        line_h = self.label.fontMetrics().lineSpacing()
        y = lg.top() + (line_h - 18) / 2.0 if lg.height() > line_h * 1.6 else (self.height() - 18) / 2.0
        p.drawPixmap(14, int(round(y)), pm)
        p.end()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._data is not None:
            self.mark_seen()
