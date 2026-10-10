"""Focus page — the active / paused / break view (spec §1.3, §1.5, §1.13, §8).

:class:`ActiveView` is embedded by ``FocusPage`` [P1] and shown while the engine is not IDLE.

* **FOCUS / PAUSED** — one centred column (max 560 px): the 280 px countdown ring, the task title
  (h1, max two lines) with a meta line (project badge · "Ends at 14:35"), the check-in strip
  (which turns into the inline "Still on it?" prompt whenever ``ctx.checkins`` emits
  ``prompt_inline``), the controls row and the capture field.
* **BREAK** — the ring in the ``break_`` colour, label-specific copy, "Skip break" /
  "End <label>" and the compact "Next block" field: Enter starts the next session right away.

The view follows ``engine.tick`` / ``engine.state_changed`` itself; ``FocusPage`` only calls
:meth:`ActiveView.focus_capture` (key "C") and :meth:`ActiveView.refresh`. Space is handled by
``FocusPage``: the view's buttons take keyboard focus by Tab only, so a mouse click never parks
focus on a button and one Space press can never toggle the pause twice.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShortcut,
    QShowEvent,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from madhura.core.checkins import MIN_REMAINING_SECONDS
from madhura.core.timefmt import fmt_clock, fmt_preset, fmt_time
from madhura.ui import animations, dialogs, fonts, theme
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import Button, IconButton
from madhura.ui.widgets.chips import Chip
from madhura.ui.widgets.inputs import TextField
from madhura.ui.widgets.labels import Badge, Caption, Label, Micro
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.progress_ring import ProgressRing
from madhura.ui.widgets.sheet import ConfirmSheet

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.core.models import PlannedTask

__all__ = [
    "ActiveView",
    "CheckInStrip",
    "ClampedText",
    "DRIFT_REASONS",
    "BREAK_COPY",
    "break_title",
    "skip_label",
    "checkin_status_text",
    "ring_font_px",
]

log = logging.getLogger(__name__)

#: Reason chips shown after "Focusing back" (same list and wording as the CheckInPopup).
DRIFT_REASONS: tuple[str, ...] = ("Phone", "Social media", "Messages", "Email", "Browsing", "Daydreaming", "Other")

#: Break view copy per break label (anything else gets :data:`DEFAULT_BREAK_COPY`).
BREAK_COPY: dict[str, str] = {
    "Break": "Stand up, stretch, look far away.",
    "Lunch": "Step away from the screen and enjoy your lunch.",
    "Meeting": "Be fully in the meeting. Your next block will wait.",
    "Admin": "Clear the small stuff, then come back to deep work.",
}
DEFAULT_BREAK_COPY = "Take a breath. Your next block will be here."

INTRO_KEY = "checkin_intro_seen"
INTRO_TEXT = "Quick honesty check — only you see this."
TASK_REQUIRED = "Name the one thing you'll work on"

COLUMN_MAX_WIDTH = 560
RING_DIAMETER = 280
RING_THICKNESS = 12
FIELD_MAX_WIDTH = 480
EXTEND_MINUTES = 5
KEEP_SWEEP_SECONDS = 5.0  # a session younger than this gets the start sweep
SWEEP_MS = 600
JUMP_MS = 400
PARKED_MS = 1500
REASON_MS = 6000
CONFIRM_MS = 1200
POLL_MS = 100
MIN_REASON_WINDOW_S = 7.0  # "Focusing back" with less prompt time left logs right away
_MAX_PLANNED_SECONDS = 480 * 60
_MIN_TOP = 24
_BOTTOM_MARGIN = 24


# =========================================================================== pure helpers


def ring_font_px(text: str) -> int:
    """Ring digit size: 60 px for ``MM:SS``, 48 px for ``H:MM:SS`` (spec §8)."""
    return 48 if text.count(":") >= 2 else 60


def break_title(label: str | None) -> str:
    """Break view heading for a break *label* ("Break", "Lunch", "Meeting", "Admin", …)."""
    return BREAK_COPY.get((label or "Break").strip() or "Break", DEFAULT_BREAK_COPY)


def skip_label(label: str | None) -> str:
    """"Skip break" for a plain break, else "End lunch" / "End meeting" / "End <label>"."""
    name = (label or "Break").strip() or "Break"
    if name == "Break":
        return "Skip break"
    if name in BREAK_COPY:
        return f"End {name.lower()}"
    return f"End {name}"


def checkin_status_text(
    *,
    enabled: bool,
    paused: bool,
    counts: Mapping[str, int],
    next_in: float | None,
    remaining: float,
) -> str:
    """The check-in strip: "Check-ins: 3 focused · 1 came back · next in ~4 min" (spec §1.3).

    "Came back" is the drifted count, framed positively; missed check-ins are not shown here.
    ``next_in`` is the service's ``seconds_until_next()``; a check-in that cannot fire any more
    (less than a minute would be left) is not announced.
    """
    if not enabled:
        return "Check-ins off"
    parts: list[str] = []
    focused = int(counts.get("focused", 0) or 0)
    drifted = int(counts.get("drifted", 0) or 0)
    if focused:
        parts.append(f"{focused} focused")
    if drifted:
        parts.append(f"{drifted} came back")
    if paused:
        return "Check-ins: " + " · ".join([*parts, "paused"]) if parts else "Check-ins paused"
    if next_in is not None and next_in >= 0:
        if remaining - next_in >= MIN_REMAINING_SECONDS:
            minutes = max(1, int(math.ceil(next_in / 60.0 - 1e-9)))
            parts.append(f"{'next' if parts else 'first'} in ~{minutes} min")
        elif not parts:
            return "No check-ins this block"
    if parts:
        return "Check-ins: " + " · ".join(parts)
    return "Check-ins on"


# =========================================================================== small widgets


class ClampedText(QWidget):
    """Text wrapped to at most ``max_lines`` lines (centred by default); the last line elides
    with "…".

    Breaks long words anywhere so a pasted URL never widens the column. The full text is the
    accessible name and, when clamped, the tooltip.
    """

    def __init__(
        self,
        role: str,
        max_lines: int = 2,
        parent: QWidget | None = None,
        *,
        token: str = "text",
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter,
        balance: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = ""
        self._max_lines = max(1, int(max_lines))
        self._token = token
        self._align = align
        self._balance = (align == Qt.AlignmentFlag.AlignHCenter) if balance is None else bool(balance)
        self._cache: tuple[int, list[str], bool] | None = None
        self.setFont(fonts.role_font(role))
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def text(self) -> str:
        """The full (unclamped) text."""
        return self._text

    def set_text(self, text: str) -> None:
        """Replace the text."""
        text = " ".join((text or "").split())
        if text == self._text:
            return
        self._text = text
        self._cache = None
        self.setAccessibleName(text)
        self.updateGeometry()
        self._sync_tooltip()
        self.update()

    def set_token(self, token: str) -> None:
        """Palette token used for the text colour (e.g. "text", "text_muted")."""
        self._token = token
        self.update()

    def set_role(self, role: str) -> None:
        """Switch the type-scale role (font)."""
        self.setFont(fonts.role_font(role))
        self._cache = None
        self.updateGeometry()
        self.update()

    def _line_height(self) -> int:
        return QFontMetrics(self.font()).lineSpacing()

    def lines(self, width: int | None = None) -> list[str]:
        """The displayed lines at *width* (default: current width)."""
        return self._layout(self.width() if width is None else width)[0]

    def is_clamped(self) -> bool:
        """True when the text did not fit and was elided."""
        return self._layout(self.width())[1]

    def _layout(self, width: int) -> tuple[list[str], bool]:
        width = max(1, int(width))
        if self._cache is not None and self._cache[0] == width:
            return self._cache[1], self._cache[2]
        text = self._text
        if not text:
            result: tuple[list[str], bool] = ([], False)
        else:
            spans = self._wrap(width)
            count = len(spans)
            if self._balance and 1 < count <= self._max_lines:
                # Balance the lines (no one-word widow): the narrowest width that still fits.
                lo, hi = max(1, width // (count + 1)), width
                while lo < hi:
                    mid = (lo + hi) // 2
                    if len(self._wrap(mid)) <= count:
                        hi = mid
                    else:
                        lo = mid + 1
                spans = self._wrap(hi)
            if len(spans) <= self._max_lines:
                result = ([text[s : s + n].strip() for s, n in spans], False)
            else:
                keep = [text[s : s + n].strip() for s, n in spans[: self._max_lines - 1]]
                rest = text[spans[self._max_lines - 1][0] :].strip()
                fm = QFontMetrics(self.font())
                keep.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
                result = (keep, True)
        self._cache = (width, result[0], result[1])
        return result

    def _wrap(self, width: int) -> list[tuple[int, int]]:
        """(start, length) of the wrapped lines at *width* (at most ``max_lines + 1``)."""
        layout = QTextLayout(self._text, self.font())
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(opt)
        spans: list[tuple[int, int]] = []
        layout.beginLayout()
        while len(spans) <= self._max_lines:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            spans.append((line.textStart(), line.textLength()))
        layout.endLayout()
        return spans

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return max(1, len(self._layout(width)[0])) * self._line_height()

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        w = min(COLUMN_MAX_WIDTH, fm.horizontalAdvance(self._text) + 4)
        return QSize(max(1, w), self.heightForWidth(self.width() if self.width() > 1 else w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self._line_height())

    def _sync_tooltip(self) -> None:
        self.setToolTip(self._text if self.width() > 1 and self.is_clamped() else "")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_tooltip()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._cache = None

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        lines = self.lines()
        if not lines:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self.font())
        p.setPen(theme.palette().color(self._token))
        lh = self._line_height()
        y = (self.height() - lh * len(lines)) / 2.0
        flags = int(self._align | Qt.AlignmentFlag.AlignVCenter)
        for line in lines:
            p.drawText(QRectF(0, y, self.width(), lh), flags, line)
            y += lh
        p.end()


class _TimeoutBar(QWidget):
    """Thin (2 px) countdown bar: track + accent fill for the remaining fraction."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fraction = 1.0
        self.setFixedHeight(2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Time left to answer")

    def fraction(self) -> float:
        """Remaining fraction 0..1."""
        return self._fraction

    def set_fraction(self, value: float) -> None:
        """Set the remaining fraction; repaints only when the drawn width changes."""
        value = max(0.0, min(1.0, float(value)))
        old_px = int(round(self._fraction * self.width()))
        self._fraction = value
        if int(round(value * self.width())) != old_px:
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        fill_rounded(p, r, 1.0, pal.track)
        if self._fraction > 0:
            fill_rounded(p, QRectF(r.x(), r.y(), r.width() * self._fraction, r.height()), 1.0, pal.accent)
        p.end()


class _PromptFrame(QWidget):
    """Rounded, quietly raised container for the inline prompt."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 10, 16, 10)
        self.body.setSpacing(8)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill_rounded(p, QRectF(self.rect()), 12.0, pal.surface, pal.border_strong if pal.is_dark else pal.border)
        p.end()


def _tab_only(*widgets: QWidget) -> None:
    """Keyboard (Tab) focus only: a mouse click never moves focus onto these widgets."""
    for w in widgets:
        w.setFocusPolicy(Qt.FocusPolicy.TabFocus)


# =========================================================================== check-in strip


class CheckInStrip(QWidget):
    """The check-in status line that becomes the inline "Still on it?" prompt.

    Phases: ``"status"`` (caption) → ``"prompt"`` (question + Focused / Focusing back + the
    service's timeout bar) → optionally ``"reason"`` (reason chips, 6 s bar) → ``"confirm"``
    (1.2 s "✓ Nice — next check-in in ~7 min" / "Back to <task>.") → ``"status"``.
    Answers go to ``ctx.checkins.answer()``; ``prompt_closed`` (timeout, pause, end) hides an
    unanswered prompt without recording anything here.
    """

    phaseChanged = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._phase = ""
        self._counts: dict[str, int] = {"focused": 0, "drifted": 0, "missed": 0}
        self._counts_sid: int | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.status_label = Caption("", self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFont(fonts.font(12, 500, tabular=True))
        self.status_label.setMinimumHeight(24)
        lay.addWidget(self.status_label)

        self.frame = _PromptFrame(self)
        self.frame.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        frame_row = QHBoxLayout()
        frame_row.setContentsMargins(0, 0, 0, 0)
        frame_row.addStretch(1)
        frame_row.addWidget(self.frame)
        frame_row.addStretch(1)
        lay.addLayout(frame_row)

        # -- question row
        self.question = QWidget(self.frame)
        q = QHBoxLayout(self.question)
        q.setContentsMargins(0, 0, 0, 0)
        q.setSpacing(8)
        self.question_label = Label("Still on it?", "label", self.question)
        self.question_label.setFont(fonts.font(14, 600))
        self.focused_button = Button("Focused", "primary", size="md", parent=self.question)
        self.focused_button.setToolTip("Focused · F")
        self.focused_button.setAccessibleDescription("Shortcut: F")
        self.back_button = Button("Focusing back", "secondary", size="md", parent=self.question)
        self.back_button.setToolTip("Focusing back · B")
        self.back_button.setAccessibleDescription("Shortcut: B")
        _tab_only(self.focused_button, self.back_button)
        q.addWidget(self.question_label)
        q.addSpacing(12)
        q.addWidget(self.focused_button)
        q.addWidget(self.back_button)
        self.frame.body.addWidget(self.question)

        self.intro_label = Caption(INTRO_TEXT, self.frame)
        self.intro_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame.body.addWidget(self.intro_label)

        # -- reason chips
        self.reason_box = QWidget(self.frame)
        r = QVBoxLayout(self.reason_box)
        r.setContentsMargins(0, 2, 0, 0)
        r.setSpacing(8)
        self.reason_label = Label("What pulled you? (optional)", "label", self.reason_box)
        self.reason_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r.addWidget(self.reason_label)
        self.reason_chips: list[Chip] = []
        for row_items in (DRIFT_REASONS[:4], DRIFT_REASONS[4:]):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addStretch(1)
            for reason in row_items:
                chip = Chip(reason, checkable=False, size="sm", parent=self.reason_box)
                chip.clicked.connect(lambda _c=False, value=reason: self._on_reason(value))
                _tab_only(chip)
                self.reason_chips.append(chip)
                row.addWidget(chip)
            row.addStretch(1)
            r.addLayout(row)
        self.frame.body.addWidget(self.reason_box)

        self.bar = _TimeoutBar(self.frame)
        self.frame.body.addWidget(self.bar)

        # -- confirmation
        self.confirm_label = Label("", "label", self)
        self.confirm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.confirm_label.setMinimumHeight(24)
        lay.addWidget(self.confirm_label)

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._update_bar)
        self._reason_timer = QTimer(self)
        self._reason_timer.setSingleShot(True)
        self._reason_timer.setInterval(REASON_MS)
        self._reason_timer.timeout.connect(lambda: self._answer("drifted", None))
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(CONFIRM_MS)
        self._confirm_timer.timeout.connect(self._end_confirm)

        self.focused_button.clicked.connect(self.answer_focused)
        self.back_button.clicked.connect(self.answer_back)
        self._set_phase("status")

    # ------------------------------------------------------------------ state
    def phase(self) -> str:
        """"status" | "prompt" | "reason" | "confirm"."""
        return self._phase

    def is_prompting(self) -> bool:
        """True while the question or the reason chips are shown (unanswered)."""
        return self._phase in ("prompt", "reason")

    def counts(self) -> dict[str, int]:
        """Cached check-in counts of the current session."""
        return dict(self._counts)

    def _set_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self.status_label.setVisible(phase == "status")
        self.frame.setVisible(phase in ("prompt", "reason"))
        self.question.setVisible(phase == "prompt")
        self.reason_box.setVisible(phase == "reason")
        self.confirm_label.setVisible(phase == "confirm")
        if phase not in ("prompt", "reason"):
            self.intro_label.hide()
            self._poll.stop()
        else:
            self._update_bar()
            self._poll.start()
        self.updateGeometry()
        self.phaseChanged.emit(phase)

    # ------------------------------------------------------------------ status line
    def reload_counts(self) -> None:
        """Re-read the session's check-in counts (after answers / misses / a new session)."""
        ctx = self._ctx
        sid = ctx.engine.session_id
        self._counts_sid = sid
        counts: dict[str, int] = {"focused": 0, "drifted": 0, "missed": 0}
        if sid is not None:
            ck = ctx.checkins
            try:
                raw = ck.counts() if ck is not None else ctx.repo.checkin_counts(sid)
            except sqlite3.Error:
                log.exception("Could not read check-in counts")
                raw = {}
            for key in counts:
                counts[key] = int(raw.get(key, 0) or 0)
        self._counts = counts

    def refresh_status(self) -> None:
        """Recompute the status caption (cheap; called on every engine tick)."""
        ctx = self._ctx
        engine = ctx.engine
        if engine.session_id != self._counts_sid:
            self.reload_counts()
        state = str(engine.state)
        ck = ctx.checkins
        next_in: float | None = None
        if ck is not None and state == "focus":
            try:
                next_in = ck.seconds_until_next()
            except (RuntimeError, sqlite3.Error):
                log.debug("seconds_until_next failed", exc_info=True)
        text = checkin_status_text(
            enabled=bool(ctx.settings.get("checkin_enabled")) and ck is not None,
            paused=state == "paused",
            counts=self._counts,
            next_in=next_in,
            remaining=engine.remaining_seconds,
        )
        if text != self.status_label.text():
            self.status_label.setText(text)
            self.status_label.setAccessibleName(text)

    # ------------------------------------------------------------------ prompt
    def show_prompt(self) -> None:
        """Turn the strip into "Still on it?" (ignored unless the engine is focusing)."""
        if str(self._ctx.engine.state) != "focus" or self.is_prompting():
            return
        self._confirm_timer.stop()
        self._reason_timer.stop()
        repo = self._ctx.repo
        show_intro = False
        try:
            if not repo.kv_get(INTRO_KEY):
                show_intro = True
                repo.kv_set(INTRO_KEY, True)
        except sqlite3.Error:
            log.debug("intro kv failed", exc_info=True)
        self._set_phase("prompt")
        self.intro_label.setVisible(show_intro)

    def cancel(self) -> None:
        """Hide an unanswered prompt without recording anything (prompt_closed / pause / end)."""
        if self.is_prompting():
            self._reason_timer.stop()
            self._set_phase("status")
            self.reload_counts()
            self.refresh_status()

    def answer_focused(self) -> None:
        """"Focused" (button or F)."""
        if self._phase == "prompt":
            self._answer("focused", None)

    def answer_back(self) -> None:
        """"Focusing back" (button or B): ask what pulled you, or log right away when the prompt
        is about to time out."""
        if self._phase != "prompt":
            return
        begin_reason = getattr(self._ctx.checkins, "begin_reason", None)
        if callable(begin_reason):
            begin_reason()  # the answer is "drifted": the service stops its timeout
        else:
            left = self._prompt_seconds_left()
            if left is not None and left < MIN_REASON_WINDOW_S:
                self._answer("drifted", None)
                return
        self.intro_label.hide()
        self._set_phase("reason")
        self._reason_timer.start()
        self._update_bar()

    def _on_reason(self, reason: str) -> None:
        if self._phase == "reason":
            self._answer("drifted", reason)

    def _prompt_seconds_left(self) -> float | None:
        ck = self._ctx.checkins
        if ck is None:
            return None
        progress = ck.timeout_progress()
        if progress is None:
            return None
        return float(progress) * float(self._ctx.settings.get("checkin_timeout_seconds"))

    def _answer(self, response: str, reason: str | None) -> None:
        self._reason_timer.stop()
        task = self._ctx.engine.task
        self._set_phase("confirm")  # before answer(): a prompt_closed it triggers is ignored
        ck = self._ctx.checkins
        if ck is not None:
            ck.answer(response, reason)
        self.reload_counts()
        if response == "focused":
            secs = None
            if ck is not None:
                secs = ck.seconds_until_next()
            if secs is not None and secs > 0:
                text = f"✓ Nice — next check-in in ~{max(1, int(math.ceil(secs / 60.0 - 1e-9)))} min"
            else:
                text = "✓ Nice — keep going"
            self.confirm_label.set_tone("success")
        else:
            fm = QFontMetrics(self.confirm_label.font())
            shown = fm.elidedText(task, Qt.TextElideMode.ElideRight, 360) if task else "your task"
            text = f"Back to {shown}."
            self.confirm_label.set_tone(None)
        self.confirm_label.setText(text)
        self.confirm_label.setAccessibleName(text)
        self._confirm_timer.start()

    def _end_confirm(self) -> None:
        if self._phase == "confirm":
            self._set_phase("status")
            self.refresh_status()

    def _update_bar(self) -> None:
        if self._phase == "reason":
            left = self._reason_timer.remainingTime() if self._reason_timer.isActive() else 0
            self.bar.show()
            self.bar.set_fraction(left / float(REASON_MS))
            return
        if self._phase != "prompt":
            return
        ck = self._ctx.checkins
        progress = ck.timeout_progress() if ck is not None else None
        self.bar.setVisible(progress is not None)
        if progress is not None:
            self.bar.set_fraction(float(progress))


# =========================================================================== the view


class ActiveView(QWidget):
    """Running-session view (FOCUS / PAUSED / BREAK) of the Focus page (see module docstring).

    Public API used by ``FocusPage``: :meth:`focus_capture` and :meth:`refresh`; the inline
    check-in prompt can also be driven directly with :meth:`show_checkin_prompt` /
    :meth:`hide_checkin_prompt` (the view already listens to ``ctx.checkins``).
    """

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._mode = ""
        self._session_id: int | None = None
        self._sweep_pending = False
        self._bound_ck: Any = None
        self._a11y_key: tuple[Any, ...] | None = None
        self._next_task: PlannedTask | None = None
        self._next_edited = False
        self.setObjectName("mhActiveView")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setAutoFillBackground(False)
        outer.addWidget(self._scroll)

        content = QWidget()
        content.setObjectName("mhActiveContent")
        content.setAutoFillBackground(False)
        content.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(32, 0, 32, _BOTTOM_MARGIN)
        self._content_layout.setSpacing(0)
        self._top_spacer = QSpacerItem(0, 64, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._content_layout.addItem(self._top_spacer)
        centre = QHBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.addStretch(1)
        self._column = QWidget(content)
        self._column.setMaximumWidth(COLUMN_MAX_WIDTH)
        self._column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        centre.addWidget(self._column, 100)
        centre.addStretch(1)
        self._content_layout.addLayout(centre)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(content)
        content.setAutoFillBackground(False)  # QScrollArea.setWidget() turns it on

        col = QVBoxLayout(self._column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._build_ring(col)
        col.addSpacing(28)
        self._build_titles(col)
        self._strip_gap_top = QSpacerItem(0, 16, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        col.addItem(self._strip_gap_top)
        self.checkin_strip = CheckInStrip(ctx, self._column)
        col.addWidget(self.checkin_strip)
        self._controls_gap = QSpacerItem(0, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        col.addItem(self._controls_gap)
        self._build_controls(col)
        col.addSpacing(24)
        self._build_capture(col)
        self._build_next_block(col)

        # keyboard: F / B answer the inline prompt (enabled only while it is visible)
        self._key_focused = QShortcut(QKeySequence("F"), self)
        self._key_back = QShortcut(QKeySequence("B"), self)
        for sc, slot in ((self._key_focused, self._on_key_focused), (self._key_back, self._on_key_back)):
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.setAutoRepeat(False)
            sc.setEnabled(False)
            sc.activated.connect(slot)
        self.checkin_strip.phaseChanged.connect(self._on_strip_phase)

        self._sweep_timer = QTimer(self)
        self._sweep_timer.setSingleShot(True)
        self._sweep_timer.setInterval(0)
        self._sweep_timer.timeout.connect(self._run_sweep)
        self._parked_timer = QTimer(self)
        self._parked_timer.setSingleShot(True)
        self._parked_timer.setInterval(PARKED_MS)
        self._parked_timer.timeout.connect(lambda: self.parked_label.setText(""))

        engine = ctx.engine
        engine.tick.connect(self._on_tick)
        engine.state_changed.connect(self._on_state_changed)
        engine.session_extended.connect(self._on_extended)
        engine.break_started.connect(self._on_break_started)
        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.settings.changed.connect(self._on_setting)
        ctx.theme.changed.connect(self._on_theme)
        ctx.theme.motion_changed.connect(self._on_motion_changed)
        self._watch_sheets()
        self.refresh()

    # ================================================================== construction
    def _build_ring(self, col: QVBoxLayout) -> None:
        self.ring = ProgressRing(RING_DIAMETER, RING_THICKNESS, self._column)
        self.ring.set_center_font(fonts.display_font(60, 300))
        self.ring.set_caption_font(fonts.font(13, 500))
        self._ring_px = 60
        col.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)

    def _build_titles(self, col: QVBoxLayout) -> None:
        self.title = ClampedText("h1", 2, self._column)
        col.addWidget(self.title)
        self.break_title = ClampedText("h2", 2, self._column)
        col.addWidget(self.break_title)
        col.addSpacing(8)
        meta = QWidget(self._column)
        meta.setMinimumHeight(22)
        row = QHBoxLayout(meta)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.project_badge = Badge("", "neutral", meta)
        self.meta_dot = Caption("·", meta)
        self.meta_label = Caption("", meta)
        self.meta_label.setFont(fonts.font(12, 400, tabular=True))
        row.addWidget(self.project_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.meta_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.meta_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.meta_row = meta
        col.addWidget(meta)

    def _build_controls(self, col: QVBoxLayout) -> None:
        self.controls = QWidget(self._column)
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        self.pause_button = Button("Pause", "secondary", icon="pause", size="lg", parent=self.controls)
        self.pause_button.setMinimumWidth(120)
        self.extend_button = Button(f"+{EXTEND_MINUTES} min", "secondary", size="lg", parent=self.controls)
        self.extend_button.setToolTip(f"Add {EXTEND_MINUTES} minutes")
        self.finish_button = Button("Finish", "secondary", icon="check", size="lg", parent=self.controls)
        self.finish_button.setToolTip("Finish · Ctrl+Shift+F")
        self.hide_button = IconButton("eye_off", "Hide to mini timer · Ctrl+H", 40, self.controls)
        self.more_button = IconButton("more", "More actions", 40, self.controls)
        _tab_only(self.pause_button, self.extend_button, self.finish_button, self.hide_button, self.more_button)
        for w in (self.pause_button, self.extend_button, self.finish_button):
            row.addWidget(w)
        row.addSpacing(4)
        row.addWidget(self.hide_button)
        row.addWidget(self.more_button)
        row.addStretch(1)
        col.addWidget(self.controls)

        self.break_controls = QWidget(self._column)
        brow = QHBoxLayout(self.break_controls)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(8)
        brow.addStretch(1)
        self.skip_button = Button("Skip break", "secondary", icon="skip", size="lg", parent=self.break_controls)
        self.skip_button.setMinimumWidth(120)
        self.break_hide_button = IconButton("eye_off", "Hide to mini timer · Ctrl+H", 40, self.break_controls)
        _tab_only(self.skip_button, self.break_hide_button)
        brow.addWidget(self.skip_button)
        brow.addSpacing(4)
        brow.addWidget(self.break_hide_button)
        brow.addStretch(1)
        col.addWidget(self.break_controls)

        engine = self._ctx.engine
        self.pause_button.clicked.connect(engine.toggle_pause)
        self.extend_button.clicked.connect(lambda: engine.extend(EXTEND_MINUTES))
        self.finish_button.clicked.connect(engine.finish)
        self.skip_button.clicked.connect(engine.skip_break)
        self.hide_button.clicked.connect(self.hide_to_mini)
        self.break_hide_button.clicked.connect(self.hide_to_mini)
        self.more_button.clicked.connect(self._open_more_menu)

    def _build_capture(self, col: QVBoxLayout) -> None:
        self.capture_box = QWidget(self._column)
        v = QVBoxLayout(self.capture_box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.capture = TextField("Capture a stray thought — deal with it later", icon="note", height=40, parent=self.capture_box)
        self.capture.setMaximumWidth(FIELD_MAX_WIDTH)
        self.capture.line_edit.setAccessibleName("Capture a stray thought")
        self.capture.line_edit.setToolTip("Park it for later · C")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.capture, 100)
        row.addStretch(1)
        v.addLayout(row)
        self.parked_label = Caption("", self.capture_box, tone="success")
        self.parked_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.parked_label.setFixedHeight(18)
        v.addWidget(self.parked_label)
        col.addWidget(self.capture_box)
        self.capture.returnPressed.connect(self._save_capture)
        self.capture.line_edit.installEventFilter(self)

    def _build_next_block(self, col: QVBoxLayout) -> None:
        self.next_box = QWidget(self._column)
        v = QVBoxLayout(self.next_box)
        v.setSpacing(8)
        v.setContentsMargins(0, 8, 0, 0)
        self.next_label = Micro("Next block", self.next_box)
        self.next_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.next_label)
        self.next_field = TextField("What will you focus on next?", icon="target", height=40, parent=self.next_box)
        self.next_field.setMaximumWidth(FIELD_MAX_WIDTH)
        self.next_field.line_edit.setAccessibleName("Next block")
        trailing = QWidget()
        t = QHBoxLayout(trailing)
        t.setContentsMargins(0, 0, 0, 0)
        t.setSpacing(4)
        self.next_minutes = Badge("", "neutral", trailing)
        self.next_start = IconButton("play", "Start next block · Enter", 28, trailing, variant="ghost")
        _tab_only(self.next_start)
        t.addWidget(self.next_minutes, 0, Qt.AlignmentFlag.AlignVCenter)
        t.addWidget(self.next_start, 0, Qt.AlignmentFlag.AlignVCenter)
        self.next_field.set_trailing_widget(trailing)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.next_field, 100)
        row.addStretch(1)
        v.addLayout(row)
        col.addWidget(self.next_box)
        self.next_field.returnPressed.connect(self.start_next_block)
        self.next_field.textEdited.connect(self._on_next_edited)
        self.next_field.textChanged.connect(lambda _t: self._sync_next_minutes())
        self.next_start.clicked.connect(self.start_next_block)
        self.next_field.line_edit.installEventFilter(self)

    def _watch_sheets(self) -> None:
        host = getattr(self._ctx.window, "sheet_host", None)
        signal = getattr(host, "activeChanged", None)
        if signal is not None:
            signal.connect(self._on_sheet_active)

    def _on_sheet_active(self, _active: bool) -> None:
        self._sync_shortcuts()

    # ================================================================== public API
    def mode(self) -> str:
        """"focus" (FOCUS/PAUSED), "break" or "idle"."""
        return self._mode

    def focus_capture(self) -> None:
        """Key "C": focus the capture field (FOCUS/PAUSED only; never while a sheet is open)."""
        if self._mode != "focus" or self._sheet_open():
            return
        self.capture.line_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def hide_to_mini(self) -> None:
        """"Hide to mini timer" (Ctrl+H): hide the main window; the pill takes over."""
        window = self._ctx.window
        if window is not None and str(self._ctx.engine.state) != "idle":
            window.hide_main()

    def on_shown(self) -> None:
        """FocusPage hook: the page became visible."""
        self.refresh()

    def start_from_input(self) -> int | None:
        """Ctrl+Enter during a break: start the "Next block" (FocusPage hook)."""
        if self._mode != "break":
            return None
        return self.start_next_block()

    def focus_task_input(self) -> None:
        """Ctrl+N / "Plan now" during a break: focus the "Next block" field (FocusPage hook)."""
        if self._mode == "break" and not self._sheet_open():
            self.next_field.line_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.next_field.line_edit.selectAll()

    def show_checkin_prompt(self) -> None:
        """Show the inline "Still on it?" prompt (normally triggered by ``prompt_inline``)."""
        self.checkin_strip.show_prompt()

    def hide_checkin_prompt(self) -> None:
        """Hide an unanswered inline prompt without recording (``prompt_closed``)."""
        self.checkin_strip.cancel()

    def refresh(self) -> None:
        """Full sync with the engine: mode, ring style, labels, buttons and the strip."""
        self._bind_checkins()
        engine = self._ctx.engine
        state = str(engine.state)
        mode = "focus" if state in ("focus", "paused") else "break" if state == "break" else "idle"
        if mode != self._mode:
            self._set_mode(mode)
        if mode == "focus":
            self._sync_focus_static(state)
        elif mode == "break":
            self._sync_break_static()
        self._apply_ring_style()
        self._sync_dynamic()

    # ================================================================== engine reactions
    def _on_state_changed(self, state: str) -> None:
        engine = self._ctx.engine
        if state != "focus":
            self.checkin_strip.cancel()
        sid = engine.session_id
        if state in ("focus", "paused") and sid is not None and sid != self._session_id:
            self._session_id = sid
            self.checkin_strip.reload_counts()
            if self._is_fresh_session():
                self._sweep_pending = True
                self.ring.set_progress(0.0)
                self._sweep_timer.start()
        elif state not in ("focus", "paused"):
            self._session_id = None
        self.refresh()

    def _on_tick(self) -> None:
        if self._mode == "idle":
            return
        self._sync_dynamic()

    def _on_extended(self, _seconds: int) -> None:
        self.refresh()

    def _on_motion_changed(self, _reduce: bool) -> None:
        self._apply_ring_style()

    def _on_break_started(self, _total: int) -> None:
        self._next_edited = False
        if self._mode == "break":
            self._prefill_next_block(force=True)

    def _on_data_changed(self, kind: str) -> None:
        if kind in ("checkins", "all") and self._mode == "focus":
            self.checkin_strip.reload_counts()
            self.checkin_strip.refresh_status()
        if kind in ("tasks", "all") and self._mode == "break":
            self._prefill_next_block(force=False)

    def _on_setting(self, key: str) -> None:
        if key in ("checkin_enabled", "checkin_interval_minutes", "time_format", "default_minutes", "day_start_hour"):
            self.refresh()

    def _on_theme(self) -> None:
        self._apply_ring_style()
        self._sync_project_badge()
        self.update()

    def _is_fresh_session(self) -> bool:
        started = self._ctx.engine.started_at
        if started is None:
            return False
        age = (self._ctx.clock.now() - started).total_seconds()
        return -1.0 <= age <= KEEP_SWEEP_SECONDS

    def _run_sweep(self) -> None:
        if not self._sweep_pending:
            return
        self._sweep_pending = False
        frac = self._ring_fraction()
        if self.ring.isVisible() and animations.motion_enabled():
            self.ring.sweep_in(frac, SWEEP_MS)
        else:
            self.ring.set_progress(frac)

    # ================================================================== check-in service
    def _bind_checkins(self) -> None:
        """(Re)connect to ``ctx.checkins`` — services start after the window is built."""
        ck = self._ctx.checkins
        if ck is self._bound_ck:
            return
        old = self._bound_ck
        if old is not None:
            for sig, slot in (
                (getattr(old, "prompt_inline", None), self._on_prompt_inline),
                (getattr(old, "prompt_closed", None), self._on_prompt_closed),
                (getattr(old, "answered", None), self._on_answered),
            ):
                if sig is not None:
                    try:
                        sig.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
        self._bound_ck = ck
        if ck is None:
            return
        for name, slot in (("prompt_inline", self._on_prompt_inline), ("prompt_closed", self._on_prompt_closed), ("answered", self._on_answered)):
            sig = getattr(ck, name, None)
            if sig is not None:
                sig.connect(slot)

    def _on_prompt_inline(self) -> None:
        if self._mode != "focus":
            self.refresh()
        self.checkin_strip.show_prompt()

    def _on_prompt_closed(self) -> None:
        self.checkin_strip.cancel()

    def _on_answered(self, _response: str) -> None:
        if self.checkin_strip.is_prompting():
            # Answered elsewhere (the popup, or the service itself): the inline prompt is moot.
            self.checkin_strip.cancel()
        self.checkin_strip.reload_counts()
        if self.checkin_strip.phase() == "status":
            self.checkin_strip.refresh_status()

    def _on_strip_phase(self, _phase: str) -> None:
        self._sync_shortcuts()
        self._update_top_spacer()

    def _sync_shortcuts(self) -> None:
        on = self.checkin_strip.phase() == "prompt" and not self._sheet_open()
        self._key_focused.setEnabled(on)
        self._key_back.setEnabled(on)

    def _on_key_focused(self) -> None:
        if not self._sheet_open():
            self.checkin_strip.answer_focused()

    def _on_key_back(self) -> None:
        if not self._sheet_open():
            self.checkin_strip.answer_back()

    def _sheet_open(self) -> bool:
        window = self._ctx.window
        try:
            return bool(window is not None and window.has_sheet())
        except RuntimeError:
            return False

    # ================================================================== syncing
    def _set_mode(self, mode: str) -> None:
        old = self._mode
        self._mode = mode
        focus_mode = mode == "focus"
        break_mode = mode == "break"
        leaving: list[QWidget] = []
        if not focus_mode:
            leaving += [self.capture_box, self.controls, self.checkin_strip]
        if not break_mode:
            leaving += [self.next_box, self.break_controls]
        self._move_focus_out_of(leaving)
        for w in (self.title, self.checkin_strip, self.controls, self.capture_box):
            w.setVisible(focus_mode)
        for w in (self.break_title, self.break_controls, self.next_box):
            w.setVisible(break_mode)
        self._strip_gap_top.changeSize(0, 16 if focus_mode else 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._controls_gap.changeSize(0, 20 if focus_mode else 24, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._column.layout().invalidate()
        if break_mode and old != "break":
            self._next_edited = False
            self.next_field.set_error(None)
            self._prefill_next_block(force=True)
        if not focus_mode:
            self.parked_label.setText("")
            self.capture.set_error(None)
        self._a11y_key = None
        self._update_top_spacer()

    def _move_focus_out_of(self, containers: list[QWidget]) -> None:
        fw = QApplication.focusWidget()
        if fw is None:
            return
        if any(c is fw or c.isAncestorOf(fw) for c in containers):
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _sync_focus_static(self, state: str) -> None:
        engine = self._ctx.engine
        self.title.set_text(engine.task)
        self._sync_project_badge()
        paused = state == "paused"
        if paused:
            self.pause_button.setText("Resume")
            self.pause_button.set_variant("primary")
            self.pause_button.set_icon_name("play")
            self.pause_button.setToolTip("Resume · Space")
        else:
            self.pause_button.setText("Pause")
            self.pause_button.set_variant("secondary")
            self.pause_button.set_icon_name("pause")
            self.pause_button.setToolTip("Pause · Space")
        can_extend = engine.planned_seconds < _MAX_PLANNED_SECONDS
        self.extend_button.setEnabled(can_extend)
        self.extend_button.setToolTip(f"Add {EXTEND_MINUTES} minutes" if can_extend else "Sessions are capped at 8 hours")
        self._update_top_spacer()

    def _sync_project_badge(self) -> None:
        project = self._ctx.engine.project if self._mode == "focus" else None
        if project:
            self.project_badge.set_text(project)
            self.project_badge.set_dot(theme.project_color(project))
            self.project_badge.setToolTip(f"Project: {project}")
        self.project_badge.setVisible(bool(project))
        self.meta_dot.setVisible(bool(project) and bool(self.meta_label.text()))

    def _sync_break_static(self) -> None:
        label = self._ctx.engine.break_label or "Break"
        self._sync_project_badge()  # breaks have no project
        self.break_title.set_text(break_title(label))
        text = skip_label(label)
        if self.skip_button.text() != text:
            self.skip_button.setText(text)
        self._update_top_spacer()

    def _ring_fraction(self) -> float:
        engine = self._ctx.engine
        state = str(engine.state)
        if state == "break":
            total = max(1, engine.break_total_seconds)
            return max(0.0, min(1.0, engine.break_remaining_seconds / total))
        if state in ("focus", "paused"):
            total = max(1, engine.planned_seconds)
            return max(0.0, min(1.0, engine.remaining_seconds / total))
        return 0.0

    def _apply_ring_style(self) -> None:
        engine = self._ctx.engine
        state = str(engine.state)
        pal = theme.palette()
        ring = self.ring
        if state == "paused":
            ring.set_color(pal.text_subtle)
            ring.set_text_color(pal.text_muted)
            ring.set_caption("Paused")
            ring.set_breathing(True)
        elif state == "break":
            ring.set_color(pal.break_)
            ring.set_text_color(None)
            ring.set_caption(engine.break_label or "Break")
            ring.set_breathing(False)
        else:
            ring.set_color(None)
            ring.set_text_color(None)
            minutes = int(round(engine.planned_seconds / 60.0))
            ring.set_caption(f"of {minutes} min" if state == "focus" else "")
            ring.set_breathing(False)

    def _sync_dynamic(self) -> None:
        engine = self._ctx.engine
        state = str(engine.state)
        if state == "break":
            remaining = engine.break_remaining_seconds
        elif state in ("focus", "paused"):
            remaining = engine.remaining_seconds
        else:
            remaining = 0.0
        text = fmt_clock(remaining) if state != "idle" else ""
        px = ring_font_px(text)
        if px != self._ring_px:
            self._ring_px = px
            self.ring.set_center_font(fonts.display_font(px, 300))
        self.ring.set_text(text)
        if not self._sweep_pending:
            self._set_ring_progress(self._ring_fraction())
        self._sync_a11y(state, remaining)
        # meta line
        use_24h = self._ctx.use_24h()
        meta = ""
        if state == "focus" and engine.ends_at is not None:
            meta = f"Ends at {fmt_time(engine.ends_at, use_24h)}"
        elif state == "paused":
            since = engine.paused_since
            meta = f"Paused at {fmt_time(since, use_24h)}" if since is not None else "Paused"
        elif state == "break" and engine.break_ends_at is not None:
            meta = f"Ends at {fmt_time(engine.break_ends_at, use_24h)}"
        if meta != self.meta_label.text():
            self.meta_label.setText(meta)
        self.meta_dot.setVisible(self.project_badge.isVisibleTo(self.meta_row) and bool(meta))
        if self._mode == "focus" and self.checkin_strip.phase() == "status":
            self.checkin_strip.refresh_status()

    def _set_ring_progress(self, frac: float) -> None:
        ring = self.ring
        target = ring.progress()
        tweening = abs(target - ring.displayed_progress()) > 1e-4
        engine = self._ctx.engine
        total = engine.break_total_seconds if str(engine.state) == "break" else engine.planned_seconds
        step = 2.5 / max(1.0, float(total))
        jump = abs(frac - target) > max(step, 0.004)
        if jump:
            ring.set_progress(frac, animate=True, duration=JUMP_MS)
        elif not tweening:
            ring.set_progress(frac)

    def _sync_a11y(self, state: str, remaining: float) -> None:
        minutes = int(math.ceil(max(0.0, remaining) / 60.0 - 1e-9))
        key = (state, minutes, self._ctx.engine.break_label)
        if key == self._a11y_key:
            return
        self._a11y_key = key
        unit = "minute" if minutes == 1 else "minutes"
        if state == "paused":
            desc = f"Paused, {minutes} {unit} left"
        elif state == "break":
            desc = f"{self._ctx.engine.break_label or 'Break'}, {minutes} {unit} left"
        elif state == "focus":
            planned = int(round(self._ctx.engine.planned_seconds / 60.0))
            desc = f"{minutes} {unit} left of {planned}"
        else:
            desc = ""
        self.ring.setAccessibleDescription(desc)

    # ================================================================== layout
    def _update_top_spacer(self) -> None:
        """Vertically centre the column on its *resting* height: the inline prompt growing or
        shrinking only pushes the controls below it, the ring never jumps."""
        lay = self._column.layout()
        if lay is None:
            return
        width = self._column.width() if self._column.width() > 1 else COLUMN_MAX_WIDTH
        lay.activate()
        height = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        extra = 0
        if self._mode == "focus" and self.checkin_strip.isVisibleTo(self._column):
            extra = max(0, self.checkin_strip.sizeHint().height() - self.checkin_strip.status_label.minimumHeight())
        height -= extra
        viewport_h = self._scroll.viewport().height()
        # Optical centre: a touch above the geometric middle.
        top = max(_MIN_TOP, int((viewport_h - height) * 0.46))
        # The transient prompt may push the column up, but never out of view.
        actual = height + extra
        overflow = top + actual + _BOTTOM_MARGIN - viewport_h
        if overflow > 0:
            top = max(_MIN_TOP, top - overflow)
        if self._top_spacer.sizeHint().height() != top:
            self._top_spacer.changeSize(0, top, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self._content_layout.invalidate()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_top_spacer()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()
        self._update_top_spacer()
        if self._sweep_pending:
            self._sweep_timer.start()

    # ================================================================== actions
    def _open_more_menu(self) -> None:
        menu = Menu(self)
        menu.add_action("Abandon session…", self.confirm_abandon, icon="trash", danger=True)
        menu.popup_below(self.more_button, align="right")

    def confirm_abandon(self) -> None:
        """"Abandon session…": confirm in a sheet, then ``engine.abandon()``."""
        engine = self._ctx.engine
        sid = engine.session_id
        if sid is None or not engine.is_active:
            return
        fm = QFontMetrics(fonts.font(14))
        task = fm.elidedText(engine.task, Qt.TextElideMode.ElideRight, 300)
        title = "Abandon this session?"
        text = f"“{task}” won't count toward your focus time."
        window = self._ctx.window
        if window is None:
            if dialogs.confirm(self, title, text, "Abandon", danger=True, cancel_text="Keep going"):
                self._abandon(sid)
            return
        sheet = ConfirmSheet(title, text, "Abandon", danger=True, cancel_text="Keep going")
        sheet.accepted.connect(lambda: self._abandon(sid))
        window.show_sheet(sheet)

    def _abandon(self, session_id: int) -> None:
        engine = self._ctx.engine
        if engine.session_id == session_id and engine.is_active:
            engine.abandon()

    def _save_capture(self) -> None:
        text = self.capture.text().strip()
        if not text:
            return
        ctx = self._ctx
        try:
            ctx.repo.add_capture(text, session_id=ctx.engine.session_id)
            count = len(ctx.repo.open_captures())
        except (ValueError, sqlite3.Error):
            log.exception("Could not save the capture")
            self.capture.set_error("Couldn't park that — try again")
            return
        self.capture.clear()
        ctx.events.data_changed.emit("captures")
        self.parked_label.setText(f"✓ Parked · {count} in parking lot")
        self.parked_label.setAccessibleName(self.parked_label.text())
        self._parked_timer.start()

    # -- next block (break view)
    def _first_up_next(self) -> PlannedTask | None:
        try:
            tasks = self._ctx.repo.tasks_for_date(self._ctx.today(), include_done=False)
        except sqlite3.Error:
            log.exception("Could not read Up next")
            return None
        return tasks[0] if tasks else None

    def _prefill_next_block(self, force: bool) -> None:
        if self._next_edited and not force:
            return
        if not force and self.next_field.line_edit.hasFocus() and self.next_field.text():
            return
        task = self._first_up_next()
        self._next_task = task
        self.next_field.set_text(task.title if task is not None else "")
        self._sync_next_minutes()

    def _on_next_edited(self, _text: str) -> None:
        self._next_edited = True

    def _next_minutes(self) -> tuple[int, PlannedTask | None]:
        task = self._next_task
        if task is not None and self.next_field.text().strip() == task.title.strip():
            return int(task.minutes), task
        return int(self._ctx.settings.get("default_minutes")), None

    def _sync_next_minutes(self) -> None:
        minutes, _task = self._next_minutes()
        self.next_minutes.set_text(fmt_preset(minutes))
        self.next_minutes.setToolTip(f"{minutes} min")

    def start_next_block(self) -> int | None:
        """Enter in "Next block": end the break and start the next session right away."""
        text = self.next_field.text().strip()
        if not text:
            self.next_field.set_error(TASK_REQUIRED)
            return None
        minutes, task = self._next_minutes()
        engine = self._ctx.engine
        if task is not None:
            sid = engine.start(text, minutes, task.project, task.id)
        else:
            sid = engine.start(text, minutes)
        if sid is None:
            log.info("Next block did not start (engine state %s)", engine.state)
        return sid

    # ================================================================== events
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
            and obj in (self.capture.line_edit, self.next_field.line_edit)
            and not self._sheet_open()
        ):
            self.setFocus(Qt.FocusReason.OtherFocusReason)  # blur; Space keeps working here
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if event.key() == Qt.Key.Key_C and mods == Qt.KeyboardModifier.NoModifier and self._mode == "focus":
            self.focus_capture()
            event.accept()
            return
        super().keyPressEvent(event)
