"""CheckInPopup — the non-activating "Still on <task>?" panel (spec §1.5, §7.3).

Phases (``phase()``):

``"question"``  caption "Check-in", "Still on <task>?", "23 min in · 22 left", primary "Focused",
                secondary "Focusing back" and a thin countdown bar driven by the service's
                ``timeout_progress()`` (hover pauses it — the service owns the timeout).
``"reason"``    "Focusing back" swaps the content (bottom edge fixed, grows upward 180 ms) to
                "What pulled you? (optional)" + reason chips; one click logs 'drifted' with the
                reason, no click logs 'drifted' without one after 6 s (hover pauses that too).
``"confirm"``   1.2 s confirmation ("Nice — next check-in in ~7 min" / "Back to <task>.") then the
                panel dismisses itself.

The panel is created and kept alive by :class:`madhura.services.checkin_service.CheckInService`,
which records every answer; the popup only renders and forwards clicks.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QSizePolicy, QWidget

from madhura.ui import animations, fonts
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
from madhura.ui.widgets.buttons import Button, LinkButton
from madhura.ui.widgets.chips import ChipGroup
from madhura.ui.widgets.labels import Caption
from madhura.ui.widgets.popup import PRIORITIES, FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "CheckInPopup",
    "DRIFT_REASONS",
    "INTRO_TEXT",
    "REASON_MS",
    "CONFIRM_MS",
    "meta_text",
    "confirmation_text",
]

DRIFT_REASONS: tuple[str, ...] = ("Phone", "Social media", "Messages", "Email", "Browsing", "Daydreaming", "Other")
INTRO_TEXT = "Quick honesty check — only you see this."
REASON_MS = 6000
CONFIRM_MS = 1200
BAR_TICK_MS = 100
REASON_RESUME_MS = 3000
#: The next check-in is only announced when it can still fire (≥ 60 s of the block left then).
_MIN_REMAINING = 60.0


def meta_text(focused_seconds: float, remaining_seconds: float) -> str:
    """``"23 min in · 22 left"`` (focused minutes floored, remaining minutes rounded up)."""
    done = max(0, int(float(focused_seconds) // 60))
    left = max(0, int(math.ceil(max(0.0, float(remaining_seconds)) / 60.0 - 1e-9)))
    return f"{done} min in · {left} left"


def confirmation_text(response: str, task: str, next_in: float | None, remaining: float) -> str:
    """The 1.2 s confirmation copy after an answer (without the leading check mark)."""
    if response == "focused":
        if next_in is not None and next_in > 0 and remaining - next_in >= _MIN_REMAINING:
            minutes = max(1, int(math.ceil(next_in / 60.0 - 1e-9)))
            return f"Nice — next check-in in ~{minutes} min"
        return "Nice — keep going"
    return f"Back to {task.strip() or 'your task'}."


class CheckInPopup(FloatingPanel):
    """Non-activating check-in panel (popup stack priority 1). See the module docstring.

    ``service`` is the owning :class:`CheckInService` (may be None for previews); the popup calls
    its ``answer()``, ``begin_reason()``, ``set_prompt_hover()``, ``timeout_progress()`` and
    ``seconds_until_next()``.
    """

    def __init__(self, ctx: AppContext | None = None, service: Any = None, *args: Any, **kwargs: Any) -> None:
        super().__init__("checkin")
        install_focus_guard()
        self.priority = PRIORITIES["checkin"]
        self._ctx = ctx
        self._service = service
        self._phase = "question"
        self._task = ""
        self._response: str | None = None
        self.setAccessibleName("Check-in")

        self.question_page = self._build_question()
        self.reason_page: QWidget | None = None
        self.confirm_page: QWidget | None = None
        self.set_content(self.question_page, animate=False)

        self._bar_timer = QTimer(self)
        self._bar_timer.setInterval(BAR_TICK_MS if animations.motion_enabled() else 500)
        self._bar_timer.timeout.connect(self._tick)
        self.reason_bar = CountdownBar(tone="neutral")
        self._reason_countdown = HoverCountdown(
            self, REASON_MS, self._reason_expired, bar=self.reason_bar, min_resume_ms=REASON_RESUME_MS
        )
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(CONFIRM_MS)
        self._confirm_timer.timeout.connect(self._confirm_done)
        self.hover_changed.connect(self._on_hover)
        self.closed.connect(self._on_closed)

    # ================================================================== building
    def _build_question(self) -> QWidget:
        page = QWidget()
        page.setObjectName("checkinQuestion")
        v = vbox(page)
        v.addLayout(panel_header("Check-in", page))
        v.addSpacing(8)
        self.title = ClampText("", 15, 600, 2, page)
        v.addWidget(self.title)
        v.addSpacing(4)
        self.meta = Caption("", page)
        self.meta.setFont(fonts.font(12, 400, tabular=True))
        v.addWidget(self.meta)
        self.intro = Caption(INTRO_TEXT, page)
        self.intro.setWordWrap(True)
        self.intro.setContentsMargins(0, 6, 0, 0)
        self.intro.hide()
        v.addWidget(self.intro)
        v.addSpacing(16)
        row = hbox(spacing=8)
        self.focused_button = Button("Focused", "primary", icon="check", size="md", parent=page)
        self.back_button = Button("Focusing back", "secondary", size="md", parent=page)
        for b in (self.focused_button, self.back_button):
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(b, 1)
        self.focused_button.setToolTip("I'm still on it")
        self.back_button.setToolTip("I drifted — bring me back")
        v.addLayout(row)
        v.addSpacing(14)
        self.bar = CountdownBar(page)
        v.addWidget(self.bar)
        self.focused_button.clicked.connect(self.answer_focused)
        self.back_button.clicked.connect(self.answer_back)
        return page

    def _build_reasons(self) -> QWidget:
        page = QWidget()
        page.setObjectName("checkinReasons")
        v = vbox(page)
        self.skip_link = LinkButton("Skip", page, size_px=12)
        self.skip_link.setToolTip("Log it without a reason")
        self.skip_link.clicked.connect(lambda: self._answer("drifted", None))
        v.addLayout(panel_header("Check-in", page, self.skip_link))
        v.addSpacing(8)
        self.reason_title = ClampText("What pulled you? (optional)", 15, 600, 2, page)
        v.addWidget(self.reason_title)
        v.addSpacing(12)
        self.reasons = ChipGroup(list(DRIFT_REASONS), exclusive=True, parent=page, size="sm", allow_empty=True, spacing=6)
        self.reasons.chipClicked.connect(lambda value: self._answer("drifted", str(value)))
        v.addWidget(self.reasons)
        v.addSpacing(14)
        self.reason_bar.setParent(page)
        v.addWidget(self.reason_bar)
        return page

    def _build_confirm(self, text: str, response: str) -> QWidget:
        page = QWidget()
        page.setObjectName("checkinConfirm")
        row = hbox(page, spacing=12)
        if response == "focused":
            self.confirm_glyph = Glyph("check", 28, page, tone="success")
        else:
            self.confirm_glyph = Glyph("target", 28, page, tone="accent")
        row.addWidget(self.confirm_glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self.confirm_label = ClampText(text, 14, 600, 2, page)
        row.addWidget(self.confirm_label, 1, Qt.AlignmentFlag.AlignVCenter)
        return page

    # ================================================================== state
    def phase(self) -> str:
        """"question" | "reason" | "confirm"."""
        return self._phase

    def task(self) -> str:
        """Task the question is about."""
        return self._task

    def response(self) -> str | None:
        """The answer given (None until answered)."""
        return self._response

    def set_question(self, task: str, focused_seconds: float, remaining_seconds: float, intro: bool = False) -> None:
        """Fill the question page ("Still on <task>?", "23 min in · 22 left", optional intro)."""
        self._task = (task or "").strip()
        self.title.set_text(f"Still on {self._task or 'your task'}?")
        self.meta.setText(meta_text(focused_seconds, remaining_seconds))
        self.intro.setVisible(bool(intro))
        self._update_bar()

    def intro_shown(self) -> bool:
        """True when the first-ever intro line is part of the question."""
        return not self.intro.isHidden()

    # ================================================================== presenting
    def present(self, priority: int | None = None) -> None:
        """Show through the popup stack (priority 1) and start the countdown bar."""
        super().present(PRIORITIES["checkin"] if priority is None else priority)
        self._bar_timer.start()
        self._update_bar()

    def close_prompt(self) -> None:
        """Close an unanswered prompt (timeout, pause, end). A confirmation keeps playing."""
        if self._phase == "confirm":
            return
        self._reason_countdown.stop()
        self._bar_timer.stop()
        self.dismiss()

    # ================================================================== answers
    def answer_focused(self) -> None:
        """"Focused" clicked."""
        if self._phase == "question":
            self._answer("focused", None)

    def answer_back(self) -> None:
        """"Focusing back" clicked: swap to the reason chips (6 s, then logged without one)."""
        if self._phase != "question":
            return
        if self._service is not None:
            self._service.begin_reason()
        if self.reason_page is None:
            self.reason_page = self._build_reasons()
        self._phase = "reason"
        self._bar_timer.stop()
        self.set_content(self.reason_page, animate=True)
        self._reason_countdown.start(REASON_MS)
        if self.underMouse():
            self._on_hover(True)

    def choose_reason(self, reason: str | None) -> None:
        """Pick a reason (as if its chip was clicked); None logs without a reason."""
        if self._phase == "reason":
            self._answer("drifted", reason)

    def _reason_expired(self) -> None:
        if self._phase == "reason":
            self._answer("drifted", None)

    def _answer(self, response: str, reason: str | None) -> None:
        if self._phase not in ("question", "reason"):
            return
        self._reason_countdown.stop()
        self._bar_timer.stop()
        self._response = response
        self._phase = "confirm"
        service = self._service
        next_in: float | None = None
        remaining = 0.0
        if service is not None:
            service.answer(response, reason)
            next_in = service.seconds_until_next()
        if self._ctx is not None:
            remaining = float(self._ctx.engine.remaining_seconds)
        text = confirmation_text(response, self._task, next_in, remaining)
        self.confirm_page = self._build_confirm(text, response)
        self.setAccessibleDescription(text)
        self.set_content(self.confirm_page, animate=True)
        self._confirm_timer.start()

    def confirmation(self) -> str:
        """The confirmation copy shown after the answer ("" before)."""
        return self.confirm_label.text() if self.confirm_page is not None else ""

    def _confirm_done(self) -> None:
        self.dismiss()

    # ================================================================== live updates
    def _tick(self) -> None:
        self._update_bar()
        ctx = self._ctx
        if ctx is not None and self._phase == "question" and ctx.engine.is_active:
            text = meta_text(ctx.engine.focused_seconds, ctx.engine.remaining_seconds)
            if text != self.meta.text():
                self.meta.setText(text)

    def _update_bar(self) -> None:
        if self._phase != "question":
            return
        progress = self._service.timeout_progress() if self._service is not None else None
        self.bar.setVisible(progress is not None or self._service is None)
        self.bar.set_fraction(1.0 if progress is None else float(progress))

    def _on_hover(self, hovered: bool) -> None:
        if self._phase == "question" and self._service is not None:
            self._service.set_prompt_hover(hovered)
            self._update_bar()
        elif self._phase == "reason":
            self._reason_countdown.set_hover(hovered)
            if self._service is not None:
                self._service.set_prompt_hover(hovered)  # holds the service's reason fallback too

    def _on_closed(self) -> None:
        self._bar_timer.stop()
        self._reason_countdown.stop()
        self._confirm_timer.stop()
