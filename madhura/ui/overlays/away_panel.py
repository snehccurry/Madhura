"""AwayPanel — offer to remove idle time from the running session (spec §1.5, §7.3).

"Away for 14 min" + [Keep as focus] [Remove 14 min]. Removing calls
``engine.discard_focused(seconds)`` (honest data); no answer within 60 s keeps the time. Hovering
pauses the countdown. Shown by :class:`madhura.services.away_service.AwayService`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QSizePolicy, QWidget

from madhura.core.timefmt import fmt_duration
from madhura.ui.overlays._panel_kit import (
    ClampText,
    CountdownBar,
    Glyph,
    HoverCountdown,
    hbox,
    install_focus_guard,
    panel_header,
    vbox,
)
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.labels import Caption
from madhura.ui.widgets.popup import PRIORITIES, FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["AwayPanel", "away_label", "TIMEOUT_MS", "CONFIRM_MS"]

TIMEOUT_MS = 60_000
CONFIRM_MS = 1600


def away_label(seconds: float) -> str:
    """``"14 min"`` below an hour, else ``"1h 12m"`` (floored to whole minutes, at least 1 min)."""
    secs = max(60.0, float(seconds))
    if secs < 3600:
        return f"{int(secs // 60)} min"
    return fmt_duration(secs)


class AwayPanel(FloatingPanel):
    """Non-activating "Away for N min" panel (popup stack priority 2).

    Emits ``decided(str)`` once: "remove" (after discarding the time), "keep" (button or the 60 s
    timeout) or "cancel" (closed because the session ended).
    """

    decided = Signal(str)

    def __init__(self, ctx: AppContext | None = None, seconds: float = 0.0, *args: Any, **kwargs: Any) -> None:
        super().__init__("away")
        install_focus_guard()
        self.priority = PRIORITIES["away"]
        self._ctx = ctx
        self._seconds = max(0.0, float(seconds))
        self._decision: str | None = None
        self.setAccessibleName("Away from your session")

        self.page = QWidget()
        v = vbox(self.page)
        top = hbox(spacing=12)
        self.glyph = Glyph("clock", 32, self.page, tone="neutral")
        top.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop)
        texts = vbox(spacing=4)
        texts.addLayout(panel_header("Welcome back", self.page))
        self.title = ClampText("", 15, 600, 2, self.page)
        texts.addWidget(self.title)
        self.body = Caption("This time counted as focus. Keep it, or remove it to keep your stats honest.", self.page)
        self.body.setWordWrap(True)
        texts.addWidget(self.body)
        top.addLayout(texts, 1)
        v.addLayout(top)
        v.addSpacing(16)
        row = hbox(spacing=8)
        self.keep_button = Button("Keep as focus", "secondary", size="md", parent=self.page)
        self.remove_button = Button("", "primary", size="md", parent=self.page)
        for b in (self.keep_button, self.remove_button):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(b, 1)
        v.addLayout(row)
        v.addSpacing(14)
        self.bar = CountdownBar(self.page, tone="neutral")
        self.bar.setAccessibleName("Keeps the time automatically when this runs out")
        v.addWidget(self.bar)
        self.set_content(self.page, animate=False)
        self._apply_seconds()

        self.keep_button.clicked.connect(self.keep)
        self.remove_button.clicked.connect(self.remove)
        self.countdown = HoverCountdown(self, TIMEOUT_MS, self.keep, bar=self.bar, min_resume_ms=10_000)
        self.hover_changed.connect(self.countdown.set_hover)
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(CONFIRM_MS)
        self._confirm_timer.timeout.connect(lambda: self.dismiss())
        self.closed.connect(self._on_closed)

    # ------------------------------------------------------------------ state
    def seconds(self) -> float:
        """Away seconds offered for removal."""
        return self._seconds

    def set_seconds(self, seconds: float) -> None:
        """Update the offered amount (e.g. a second away stretch while the panel is open)."""
        self._seconds = max(0.0, float(seconds))
        self._apply_seconds()

    def decision(self) -> str | None:
        """"remove" | "keep" | "cancel" once decided."""
        return self._decision

    def _apply_seconds(self) -> None:
        label = away_label(self._seconds)
        self.title.set_text(f"Away for {label}")
        self.remove_button.setText(f"Remove {label}")
        self.remove_button.setToolTip("Don't count this time as focus")

    # ------------------------------------------------------------------ presenting
    def present(self, priority: int | None = None) -> None:
        """Show through the popup stack and start the 60 s keep countdown."""
        super().present(PRIORITIES["away"] if priority is None else priority)
        self.countdown.start(TIMEOUT_MS)

    # ------------------------------------------------------------------ actions
    def keep(self) -> None:
        """Keep the time as focus (also the timeout default)."""
        if self._decision is not None:
            return
        self._decide("keep")
        self.dismiss()

    def remove(self) -> None:
        """Discard the away time from the running session, confirm briefly, then close."""
        if self._decision is not None:
            return
        removed = 0.0
        if self._ctx is not None:
            removed = float(self._ctx.engine.discard_focused(self._seconds))
        self._decide("remove")
        if removed <= 0:
            self.dismiss()
            return
        self._show_confirmation(removed)

    def cancel(self) -> None:
        """Close without acting (the session ended)."""
        if self._decision is None:
            self._decide("cancel")
        self.dismiss()

    def _decide(self, decision: str) -> None:
        self._decision = decision
        self.countdown.stop()
        self.decided.emit(decision)

    def _show_confirmation(self, removed: float) -> None:
        page = QWidget()
        row = hbox(page, spacing=12)
        row.addWidget(Glyph("check", 28, page, tone="success"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.confirm_label = ClampText(f"Removed {away_label(removed)} from this session.", 14, 600, 2, page)
        row.addWidget(self.confirm_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.confirm_page = page
        self.set_content(page, animate=True)
        self._confirm_timer.start()

    def _on_closed(self) -> None:
        self.countdown.stop()
        self._confirm_timer.stop()
