"""STUB — owned by [P2]. Focus page active / paused / break view (spec §1.3).

Created by the app shell [F3] with a minimal ring + controls; the focus-active agent replaces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from madhura.core.timefmt import fmt_clock, fmt_time
from madhura.ui import theme
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.labels import Caption, Label
from madhura.ui.widgets.progress_ring import ProgressRing

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["ActiveView"]


class ActiveView(QWidget):
    """Running-session view: ring, task, Pause/Resume, +5 min, Finish (break: Skip). STUB."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 32)
        outer.addStretch(1)
        self.ring = ProgressRing(280, 12, self)
        outer.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addSpacing(28)
        self.title = Label("", "h1", self)
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setWordWrap(True)
        outer.addWidget(self.title)
        self.meta = Caption("", self)
        self.meta.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.meta)
        outer.addSpacing(24)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.pause_button = Button("Pause", "secondary", size="lg", parent=self)
        self.pause_button.setMinimumWidth(120)
        self.pause_button.clicked.connect(ctx.engine.toggle_pause)
        self.extend_button = Button("+5 min", "secondary", size="lg", parent=self)
        self.extend_button.clicked.connect(lambda: ctx.engine.extend(5))
        self.finish_button = Button("Finish", "secondary", size="lg", parent=self)
        self.finish_button.clicked.connect(ctx.engine.finish)
        self.skip_button = Button("Skip break", "secondary", size="lg", parent=self)
        self.skip_button.clicked.connect(ctx.engine.skip_break)
        for b in (self.pause_button, self.extend_button, self.finish_button, self.skip_button):
            row.addWidget(b)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        ctx.engine.tick.connect(self.refresh)
        ctx.engine.state_changed.connect(self._on_state)
        ctx.theme.changed.connect(self.refresh)
        self.refresh()

    def _on_state(self, _state: str) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Sync ring, labels and buttons with the engine."""
        e = self._ctx.engine
        state = str(e.state)
        pal = theme.palette()
        on_break = state == "break"
        if on_break:
            total = max(1, e.break_total_seconds)
            remaining = e.break_remaining_seconds
            self.ring.set_color(pal.break_)
            self.ring.set_caption(e.break_label or "Break")
            self.title.setText("Stand up, stretch, look far away.")
            self.meta.setText("")
        else:
            total = max(1, e.planned_seconds)
            remaining = e.remaining_seconds
            paused = state == "paused"
            self.ring.set_color(pal.text_subtle if paused else pal.accent)
            self.ring.set_caption("Paused" if paused else f"of {round(total / 60)} min")
            self.title.setText(e.task)
            ends = e.ends_at
            self.meta.setText(f"Ends at {fmt_time(ends, self._ctx.use_24h())}" if ends else "")
            self.pause_button.setText("Resume" if paused else "Pause")
            self.pause_button.set_variant("primary" if paused else "secondary")
        self.ring.set_text(fmt_clock(remaining))
        self.ring.set_progress(max(0.0, min(1.0, remaining / total)))
        for b in (self.pause_button, self.extend_button, self.finish_button):
            b.setVisible(not on_break)
        self.skip_button.setVisible(on_break)
