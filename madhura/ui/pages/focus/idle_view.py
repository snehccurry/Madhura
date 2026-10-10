"""STUB — owned by [P1]. Focus page idle (planning) view: PlanCard, UpNextCard, TodayCard,
ParkingLotCard, IdleView (spec §1.2).

Created by the app shell [F3] with a working "Lock in" (task field → ``ctx.engine.start``); the
focus-idle agent replaces this module.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from madhura.core.timefmt import fmt_date_long, fmt_duration, fmt_time, greeting, plan_heading
from madhura.ui.widgets.banner import InlineBanner
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.inputs import TextField
from madhura.ui.widgets.labels import Caption, Label
from madhura.ui.widgets.page import PageScroll

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["PlanCard", "UpNextCard", "TodayCard", "ParkingLotCard", "IdleView"]


class PlanCard(Card):
    """"Plan your next hour" card: task field + Lock in. STUB: default duration only."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(padding=24, parent=parent)
        self._ctx = ctx
        self._title = Label("", "h2", self)
        self.body.addWidget(self._title)
        self.task_field = TextField("What will you focus on?", icon="target", height=52, parent=self)
        self.task_field.returnPressed.connect(self.start)
        self.body.addWidget(self.task_field)
        row = QHBoxLayout()
        row.setSpacing(12)
        self._helper = Caption("", self)
        row.addWidget(self._helper, 1)
        self.lock_in = Button("Lock in", "primary", icon="play", size="lg", parent=self)
        self.lock_in.setMinimumWidth(140)
        self.lock_in.clicked.connect(self.start)
        row.addWidget(self.lock_in)
        self.body.addLayout(row)
        self.refresh()

    def minutes(self) -> int:
        """Selected duration (STUB: settings.default_minutes)."""
        return int(self._ctx.settings.get("default_minutes"))

    def refresh(self) -> None:
        """Update heading and the "Ends at" preview."""
        minutes = self.minutes()
        self._title.setText(plan_heading(minutes))
        ends = self._ctx.clock.now() + timedelta(minutes=minutes)
        s = self._ctx.settings
        checkins = (
            f"check-ins every {s.get('checkin_interval_minutes')} min" if s.get("checkin_enabled") else "check-ins off"
        )
        self._helper.setText(f"Ends at {fmt_time(ends, self._ctx.use_24h())} · {checkins}")

    def start(self) -> int | None:
        """Start a session from the task field; returns the session id (None if not started)."""
        text = self.task_field.text().strip()
        if not text:
            self.task_field.set_error("Name the one thing you'll work on")
            return None
        sid = self._ctx.engine.start(text, self.minutes())
        if sid is not None:
            self.task_field.set_text("")
        return sid

    def focus_task(self) -> None:
        """Put keyboard focus in the task field."""
        self.task_field.line_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)


class UpNextCard(Card):
    """Planned tasks for today. STUB: empty card."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(title="Up next", parent=parent)
        self._ctx = ctx
        self.body.addWidget(Caption("Nothing planned yet.", self))


class TodayCard(Card):
    """Today's sessions + total. STUB: total only."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(title="Today", parent=parent)
        self._ctx = ctx
        self._caption = Caption("", self)
        self.body.addWidget(self._caption)
        self.setFixedWidth(320)
        self.refresh()

    def refresh(self) -> None:
        """Re-read today's sessions."""
        sessions = self._ctx.repo.sessions_between(*self._ctx.day_bounds())
        if not sessions:
            self._caption.setText("No sessions yet today.")
            return
        total = sum(s.focused_seconds for s in sessions)
        self._caption.setText(f"{len(sessions)} sessions · {fmt_duration(total)}")


class ParkingLotCard(Card):
    """Open captures. STUB: hidden."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(title="Parking lot", parent=parent)
        self._ctx = ctx
        self.hide()


class IdleView(PageScroll):
    """Planning view: greeting header, banners, Plan / Up next / Today cards. STUB."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(1040, parent)
        self._ctx = ctx
        header = QVBoxLayout()
        header.setSpacing(4)
        self._greeting = Label("", "h1", self.column)
        self._date = Caption("", self.column)
        header.addWidget(self._greeting)
        header.addWidget(self._date)
        self.content_layout.addLayout(header)
        self.content_layout.addSpacing(8)
        self.nudge_banner = InlineBanner("accent", "You're between blocks. Plan the next one — or snooze.", parent=self.column)
        self.nudge_banner.hide()
        self.add(self.nudge_banner)
        self.plan_card = PlanCard(ctx, self.column)
        row = QHBoxLayout()
        row.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(self.plan_card)
        self.up_next = UpNextCard(ctx, self.column)
        left.addWidget(self.up_next)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(16)
        self.today_card = TodayCard(ctx, self.column)
        right.addWidget(self.today_card)
        self.parking_lot = ParkingLotCard(ctx, self.column)
        right.addWidget(self.parking_lot)
        right.addStretch(1)
        row.addLayout(left, 1)
        row.addLayout(right)
        self.content_layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        """Re-read everything shown."""
        now = self._ctx.clock.now()
        self._greeting.setText(greeting(now))
        self._date.setText(fmt_date_long(self._ctx.today()))
        self.plan_card.refresh()
        self.today_card.refresh()

    def set_nudge_banner(self, visible: bool) -> None:
        """Show/hide the firm-nudge banner."""
        self.nudge_banner.setVisible(bool(visible))
