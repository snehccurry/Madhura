"""Session end (spec §1.4): toasts, the in-window sheet, the non-activating panel and the
pending sheet.

``FocusPage`` calls :func:`present_session_complete` for every ``engine.session_finished``:

* ``restored`` → toast "Your session on '<task>' finished while Madhura was closed." (no sheet,
  no sound);
* ``abandoned`` → toast "Session discarded", or "Too short to save" when the engine deleted the
  row (``repo.get_session(id) is None``);
* ``completed`` / ``ended_early`` → sound ``complete`` and, when the main window is active, the
  :class:`SessionCompleteSheet`; otherwise the non-activating :class:`CompletionPanel` (popup
  stack, priority 0, auto-hides after 120 s) and kv ``pending_complete`` = id, so the note /
  mark-done sheet appears the next time the main window is shown or activated
  (:func:`show_pending_complete`).
"""

from __future__ import annotations

import logging
import sqlite3
import weakref
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from madhura.core.timefmt import fmt_time
from madhura.ui import animations, fonts, theme
from madhura.ui.pages.focus.active_view import ClampedText
from madhura.ui.widgets._common import fill_rounded
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.checkbox import Checkbox
from madhura.ui.widgets.chips import ChipGroup
from madhura.ui.widgets.inputs import TextArea
from madhura.ui.widgets.labels import Badge, Caption, Micro
from madhura.ui.widgets.popup import PRIORITIES, FloatingPanel
from madhura.ui.widgets.sheet import SheetContent

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.core.models import CheckIn, PlannedTask, Session

__all__ = [
    "present_session_complete",
    "show_pending_complete",
    "SessionCompleteSheet",
    "CompletionPanel",
    "PENDING_KEY",
    "PANEL_AUTO_HIDE_MS",
    "KEEP_GOING_MINUTES",
    "BREAK_CHOICES",
    "minutes_summary",
    "restored_text",
    "score_summary",
    "break_choices",
]

log = logging.getLogger(__name__)

PENDING_KEY = "pending_complete"
PANEL_AUTO_HIDE_MS = 120_000
KEEP_GOING_MINUTES = 15
BREAK_CHOICES: tuple[int, ...] = (5, 10, 15)
COUNTED = ("completed", "ended_early")
CHECK_DRAW_MS = 400
TOAST_TEXT_PX = 420  # ToastHost elides longer text
_MIN_HOVER_RESUME_MS = 10_000


class _PerContext:
    """Mapping-like registry that keeps its value *on the context* (``ctx.flags``).

    The values (sheets, panels, watchers) reference the context themselves, so a module-level
    ``WeakKeyDictionary`` keyed by the context would never let it go; storing them on the context
    ties their lifetime to it.
    """

    def __init__(self, name: str) -> None:
        self._key = f"_session_complete_{name}"

    def get(self, ctx: Any, default: Any = None) -> Any:
        return ctx.flags.get(self._key, default)

    def setdefault(self, ctx: Any, default: Any) -> Any:
        return ctx.flags.setdefault(self._key, default)

    def pop(self, ctx: Any, default: Any = None) -> Any:
        return ctx.flags.pop(self._key, default)

    def __setitem__(self, ctx: Any, value: Any) -> None:
        ctx.flags[self._key] = value

    def __delitem__(self, ctx: Any) -> None:
        del ctx.flags[self._key]


# Live sheets / panels per context (panels are parentless: this keeps them alive).
_SHEETS = _PerContext("sheets")  # ctx → {session_id: SessionCompleteSheet}
_PANELS = _PerContext("panel")  # ctx → CompletionPanel
_WATCHERS = _PerContext("watcher")  # ctx → _PendingWatcher


# =========================================================================== text helpers


def _elide(text: str, px: int, size: int = 14, weight: int = 400) -> str:
    return QFontMetrics(fonts.font(size, weight)).elidedText(text or "", Qt.TextElideMode.ElideRight, px)


def restored_text(task: str) -> str:
    """"Your session on '<task>' finished while Madhura was closed." — the task is elided so the
    whole sentence fits one toast line."""
    tail = "’ finished while Madhura was closed."
    head = "Your session on ‘"
    if not task:
        return "Your last session finished while Madhura was closed."
    budget = TOAST_TEXT_PX - QFontMetrics(fonts.font(13, 500)).horizontalAdvance(head + tail) - 4
    if budget < 48:
        return "Your last session finished while Madhura was closed."
    return f"{head}{_elide(task, budget, 13, 500)}{tail}"


def minutes_summary(session: Session) -> str:
    """"32 of 45 min" — focused minutes (floored, honest) of the planned minutes."""
    focused = int(max(0, session.focused_seconds) // 60)
    planned = int(round(max(0, session.planned_seconds) / 60.0))
    return f"{focused} of {planned} min"


def score_summary(counts: dict[str, int]) -> tuple[str | None, str | None]:
    """("6 of 7 focused", "2 unanswered") — score over answered check-ins; misses separately."""
    focused = int(counts.get("focused", 0) or 0)
    answered = focused + int(counts.get("drifted", 0) or 0)
    missed = int(counts.get("missed", 0) or 0)
    score = f"{focused} of {answered} focused" if answered else None
    unanswered = f"{missed} unanswered" if missed else None
    return score, unanswered


def break_choices(default_minutes: int) -> list[int]:
    """Break chips: 5 / 10 / 15 plus the configured break length when it differs."""
    values = set(BREAK_CHOICES)
    if 1 <= int(default_minutes) <= 30:
        values.add(int(default_minutes))
    return sorted(values)


def _counts_of(checkins: list[CheckIn]) -> dict[str, int]:
    counts = {"focused": 0, "drifted": 0, "missed": 0}
    for c in checkins:
        key = str(c.response)
        if key in counts:
            counts[key] += 1
    return counts


def _toast(ctx: AppContext, text: str, kind: str = "info", duration_ms: int = 4000) -> None:
    window = ctx.window
    if window is not None:
        window.show_toast(text, kind, duration_ms=duration_ms)
    else:
        ctx.events.toast.emit(text, kind)


# =========================================================================== painted bits


class _CheckMark(QWidget):
    """Success check in a soft circle that draws itself (ring, then tick) in 400 ms."""

    def __init__(self, size: int = 48, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = int(size)
        self._t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(CHECK_DRAW_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self.setFixedSize(self._size, self._size)
        self.setAccessibleName("Done")

    def progress(self) -> float:
        """Drawn fraction 0..1."""
        return self._t

    def play(self) -> None:
        """Draw the check (instant with reduce-motion)."""
        self._anim.stop()
        if not animations.motion_enabled():
            self._t = 1.0
            self.update()
            return
        self._t = 0.0
        self._anim.start()

    def _on_value(self, v: object) -> None:
        self._t = float(v)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = float(self._size)
        stroke = max(1.6, s / 24.0)
        r = QRectF(stroke / 2 + 0.5, stroke / 2 + 0.5, s - stroke - 1, s - stroke - 1)
        t = self._t
        p.save()
        p.setOpacity(min(1.0, t * 2.5))
        fill_rounded(p, r, r.width() / 2.0, pal.success_soft)
        p.restore()
        ring = min(1.0, t / 0.6)
        if ring > 0:
            pen = QPen(QColor(pal.success), stroke)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if ring >= 0.999:
                p.drawEllipse(r)
            else:
                p.drawArc(r, 90 * 16, -int(round(ring * 360 * 16)))
        tick = max(0.0, min(1.0, (t - 0.35) / 0.65))
        if tick > 0:
            k = s / 48.0
            path = QPainterPath(QPointF(15.5 * k, 24.8 * k))
            path.lineTo(QPointF(21.6 * k, 30.6 * k))
            path.lineTo(QPointF(33.0 * k, 18.4 * k))
            pen = QPen(QColor(pal.success), max(2.0, 3.0 * k))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if tick < 1.0:
                length = path.length()
                w = pen.widthF()
                pen.setDashPattern([max(0.01, length * tick / w), length / w + 1])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.end()


class _Timeline(QWidget):
    """One 8 px segment per check-in, in order: focused success · drifted warning · missed track."""

    SEGMENT_H = 8
    MAX_SEGMENT_W = 44

    def __init__(self, responses: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._responses = list(responses)
        self.setFixedHeight(self.SEGMENT_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        counts = {k: self._responses.count(k) for k in ("focused", "drifted", "missed")}
        parts = [f"{counts['focused']} focused"]
        if counts["drifted"]:
            parts.append(f"{counts['drifted']} came back")
        if counts["missed"]:
            parts.append(f"{counts['missed']} unanswered")
        self.setAccessibleName("Check-in timeline")
        self.setAccessibleDescription(", ".join(parts))

    def responses(self) -> list[str]:
        """The check-in responses, oldest first."""
        return list(self._responses)

    def segment_rects(self) -> list[QRectF]:
        """Segment rectangles at the current width."""
        n = len(self._responses)
        if n == 0:
            return []
        gap = 3.0 if n <= 24 else 2.0
        w = (self.width() - gap * (n - 1)) / n
        w = max(3.0, min(float(self.MAX_SEGMENT_W), w))
        return [QRectF(i * (w + gap), 0, w, self.SEGMENT_H) for i in range(n)]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        colors = {"focused": pal.success, "drifted": pal.warning, "missed": pal.track}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for resp, rect in zip(self._responses, self.segment_rects(), strict=False):
            fill_rounded(p, rect, 2.5, colors.get(resp, pal.track))
        p.end()


def _one_shot(owner: QObject, slot: Any) -> QTimer:
    """A single-shot timer owned by *owner* (it dies with it, so *slot* never runs late)."""
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(slot)
    return timer


def _set_font(label: QLabel, size: int, weight: int = 400, *, tabular: bool = False) -> QLabel:
    label.setFont(fonts.font(size, weight, tabular))
    return label


# =========================================================================== the sheet


class SessionCompleteSheet(SheetContent):
    """In-window "Session complete" / "Session ended" sheet (440 px, padding 28).

    Shows the drawn check, minutes ("32 of 45 min"), the task, the check-in timeline with the
    score, an optional note and — for Up next sessions — "Mark '<task>' done" (checked for
    completed, unchecked for ended early). Footer: ghost "Done" (Esc) · secondary "Keep going +15"
    · primary "Take a 5-min break" (break chips 5/10/15 above) or "Plan next" when breaks are
    off (Ctrl+Enter). The note and mark-done are saved however the sheet closes.
    """

    sheet_width = 440
    padding = 28

    def __init__(
        self,
        ctx: AppContext,
        session_id: int,
        outcome: str,
        parent: QWidget | None = None,
        *,
        pending: bool = False,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.session_id = int(session_id)
        self.outcome = str(outcome)
        self.pending = bool(pending)
        self._committed = False
        self._apply_mark_done = True
        repo = ctx.repo
        self._session: Session | None = None
        self._task: PlannedTask | None = None
        checkins: list[CheckIn] = []
        try:
            self._session = repo.get_session(self.session_id)
            if self._session is not None:
                checkins = repo.checkins_for_session(self.session_id)
                if self._session.planned_task_id is not None:
                    self._task = repo.get_task(self._session.planned_task_id)
        except sqlite3.Error:
            log.exception("Could not load session %s", session_id)
        session = self._session
        completed = self.outcome == "completed"
        self._title_text = "Session complete" if completed else "Session ended"

        body = self.body
        body.setSpacing(0)
        self.check = _CheckMark(48, self)
        body.addWidget(self.check)
        body.addSpacing(16)
        self.title_label = _set_font(QLabel(self._title_text, self), 18, 600)
        self.title_label.setAccessibleName(self._title_text)
        body.addWidget(self.title_label)
        body.addSpacing(4)
        self.summary_label = _set_font(Caption(self._summary_text(), self), 13, 400, tabular=True)
        body.addWidget(self.summary_label)

        # -- task line
        body.addSpacing(20)
        task_row = QHBoxLayout()
        task_row.setContentsMargins(0, 0, 0, 0)
        task_row.setSpacing(10)
        self.task_label = ClampedText("label", 2, self, align=Qt.AlignmentFlag.AlignLeft)
        self.task_label.setFont(fonts.font(14, 600))
        self.task_label.set_text(session.task if session is not None else "")
        task_row.addWidget(self.task_label, 1)
        self.project_badge: Badge | None = None
        if session is not None and session.project:
            self.project_badge = Badge(session.project, "neutral", self, dot=theme.project_color(session.project))
            task_row.addWidget(self.project_badge, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(task_row)

        # -- check-in timeline
        self.counts = _counts_of(checkins)
        self.timeline: _Timeline | None = None
        self.score_label: QLabel | None = None
        if checkins:
            body.addSpacing(20)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(Micro("Check-ins", self))
            head.addStretch(1)
            score, unanswered = score_summary(self.counts)
            parts = [x for x in (score, unanswered) if x]
            self.score_label = _set_font(Caption(" · ".join(parts), self), 12, 500, tabular=True)
            head.addWidget(self.score_label)
            body.addLayout(head)
            body.addSpacing(10)
            self.timeline = _Timeline([str(c.response) for c in checkins], self)
            body.addWidget(self.timeline)

        # -- note
        body.addSpacing(20)
        self.note = TextArea("Add a note — what went well, what got in the way?", 3, self)
        self.note.setAccessibleName("Session note")
        if session is not None and session.note:
            self.note.set_text(session.note)
        body.addWidget(self.note)

        # -- mark done
        self.mark_done: Checkbox | None = None
        if self._task is not None:
            body.addSpacing(14)
            title = _elide(self._task.title, 300)
            self.mark_done = Checkbox(text=f"Mark ‘{title}’ done", parent=self, accessible_name=f"Mark ‘{self._task.title}’ done")
            self.mark_done.setChecked(bool(self._task.done) or completed)
            body.addWidget(self.mark_done)

        # -- break chips
        self.break_row = QWidget(self)
        br = QHBoxLayout(self.break_row)
        br.setContentsMargins(0, 18, 0, 0)
        br.setSpacing(12)
        br.addWidget(Micro("Break", self.break_row), 0, Qt.AlignmentFlag.AlignVCenter)
        default_break = int(ctx.settings.get("break_minutes"))
        choices = break_choices(default_break)
        self.break_chips = ChipGroup([(m, f"{m} min") for m in choices], value=default_break, parent=self.break_row, size="sm", wrap=False, spacing=6)
        self.break_chips.setAccessibleName("Break length")
        br.addWidget(self.break_chips, 0, Qt.AlignmentFlag.AlignVCenter)
        br.addStretch(1)
        body.addWidget(self.break_row)

        # -- footer
        self.done_button = Button("Done", "ghost", parent=self)
        self.done_button.setToolTip("Done · Esc")
        self.keep_button = Button(f"Keep going +{KEEP_GOING_MINUTES}", "secondary", parent=self)
        self.keep_button.setToolTip(f"Start another {KEEP_GOING_MINUTES} minutes on the same task")
        self.main_button = Button("", "primary", parent=self)
        self.add_footer_button(self.done_button)
        self.add_footer_button(self.keep_button)
        self.add_footer_button(self.main_button, primary=True)

        self.done_button.clicked.connect(self.close_sheet)
        self.keep_button.clicked.connect(self.keep_going)
        self.main_button.clicked.connect(self._on_primary)
        self.break_chips.selectionChanged.connect(lambda _v: self._sync_actions())
        self._check_timer = _one_shot(self, self.check.play)
        self.finished.connect(self._commit)
        ctx.engine.state_changed.connect(self._on_engine_state)
        ctx.settings.changed.connect(self._on_setting)
        ctx.theme.changed.connect(self._on_theme)
        self._sync_actions()

    # ------------------------------------------------------------------ text
    def title_text(self) -> str:
        """"Session complete" / "Session ended"."""
        return self._title_text

    def _summary_text(self) -> str:
        session = self._session
        if session is None:
            return ""
        text = minutes_summary(session)
        use_24h = self._ctx.use_24h()
        if session.started_at is not None:
            if session.ended_at is not None:
                text += f" · {fmt_time(session.started_at, use_24h)}–{fmt_time(session.ended_at, use_24h)}"
            else:
                text += f" · started {fmt_time(session.started_at, use_24h)}"
        return text

    # ------------------------------------------------------------------ actions state
    def breaks_offered(self) -> bool:
        """True when the primary action is "Take a N-min break"."""
        return bool(self._ctx.settings.get("breaks_enabled"))

    def selected_break_minutes(self) -> int:
        """Minutes of the selected break chip."""
        value = self.break_chips.value()
        return int(value) if value is not None else int(self._ctx.settings.get("break_minutes"))

    def _on_engine_state(self, _state: str) -> None:
        if not self._committed:
            self._sync_actions()

    def _on_setting(self, key: str) -> None:
        if not self._committed and key in ("breaks_enabled", "break_minutes"):
            self._sync_actions()

    def _on_theme(self) -> None:
        session = self._session
        if self.project_badge is not None and session is not None and session.project:
            self.project_badge.set_dot(theme.project_color(session.project))

    def _sync_actions(self) -> None:
        """Footer for the engine's state: idle → Done · Keep going · break/Plan next; a break
        already running (auto-start) → Done · Keep going; a session running → Done."""
        state = str(self._ctx.engine.state)
        session = self._session
        idle = state == "idle"
        can_keep = session is not None and state in ("idle", "break")
        self.keep_button.setVisible(can_keep)
        self.main_button.setVisible(idle)
        offer_break = idle and self.breaks_offered()
        self.break_row.setVisible(offer_break)
        if idle:
            self.main_button.setText(f"Take a {self.selected_break_minutes()}-min break" if offer_break else "Plan next")
            self.main_button.setToolTip(f"{self.main_button.text()} · Ctrl+Enter")
            self.done_button.set_variant("ghost")
            self.set_primary(self.main_button)
        else:
            self.done_button.set_variant("primary")
            self.set_primary(self.done_button)
        self.updateGeometry()
        host = self.host()
        relayout = getattr(host, "relayout", None)
        if callable(relayout):
            relayout()  # the card follows the new height (e.g. a session started behind the sheet)

    # ------------------------------------------------------------------ actions
    def _on_primary(self) -> None:
        if str(self._ctx.engine.state) != "idle":
            self.close_sheet()
            return
        if self.breaks_offered():
            self.take_break(self.selected_break_minutes())
        else:
            self.plan_next()

    def keep_going(self) -> None:
        """Start another 15 minutes on the same task / project / Up next item."""
        session = self._session
        if session is None:
            self.close_sheet()
            return
        self._apply_mark_done = False  # continuing means it isn't done yet
        self.close_sheet()
        self._ctx.engine.start(session.task, KEEP_GOING_MINUTES, session.project, session.planned_task_id)

    def take_break(self, minutes: int) -> None:
        """Close and start a break of *minutes*."""
        self.close_sheet()
        self._ctx.engine.start_break(int(minutes))

    def plan_next(self) -> None:
        """Close and focus the plan card's task field (after the sheet has gone)."""
        self.close_sheet()
        window = self._ctx.window
        if window is not None:
            window.show_main("focus")
            window.focus_task_input()

    # ------------------------------------------------------------------ lifecycle
    def on_shown(self) -> None:
        """Draw the check once the card has started to appear."""
        self._check_timer.start(120 if animations.motion_enabled() else 0)

    def _commit(self) -> None:
        """Save the note and mark-done (once, however the sheet closed)."""
        if self._committed:
            return
        self._committed = True
        ctx = self._ctx
        for signal, slot in (
            (ctx.engine.state_changed, self._on_engine_state),
            (ctx.settings.changed, self._on_setting),
            (ctx.theme.changed, self._on_theme),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        _forget_sheet(ctx, self)
        session = self._session
        repo = ctx.repo
        try:
            if session is not None:
                note = self.note.text().strip() or None
                if note != ((session.note or "").strip() or None):
                    repo.update_session(self.session_id, note=note)
                    ctx.events.data_changed.emit("sessions")
            task = self._task
            if task is not None and self.mark_done is not None and self._apply_mark_done:
                checked = self.mark_done.isChecked()
                current = repo.get_task(task.id)
                if current is not None and bool(current.done) != checked:
                    repo.set_task_done(task.id, checked)
                    ctx.events.data_changed.emit("tasks")
            if repo.kv_get(PENDING_KEY) == self.session_id:
                repo.kv_set(PENDING_KEY, None)
        except sqlite3.Error:
            log.exception("Could not save the session summary")


# =========================================================================== the panel


class CompletionPanel(FloatingPanel):
    """Non-activating "Nice — 60 min on <task>" panel (popup stack priority 0).

    Buttons: primary "Take a 5-min break" (or "Plan next" when breaks are off), "Keep going +15",
    "Done". Auto-hides after 120 s (hovering pauses the countdown).
    """

    def __init__(self, ctx: AppContext, session_id: int, outcome: str = "completed") -> None:
        super().__init__("completion")
        self._ctx = ctx
        self.session_id = int(session_id)
        self.outcome = str(outcome)
        self.priority = PRIORITIES["completion"]
        self._session: Session | None = None
        counts = {"focused": 0, "drifted": 0, "missed": 0}
        try:
            self._session = ctx.repo.get_session(self.session_id)
            if self._session is not None:
                counts = dict(ctx.repo.checkin_counts(self.session_id))
        except sqlite3.Error:
            log.exception("Could not load session %s", session_id)
        session = self._session

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        self.check = _CheckMark(32, content)
        top.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        minutes = int(max(0, session.focused_seconds) // 60) if session is not None else 0
        task = session.task if session is not None else ""
        self.title_label = ClampedText("label", 2, content, align=Qt.AlignmentFlag.AlignLeft)
        self.title_label.setFont(fonts.font(14, 600))
        self.title_label.set_text(f"Nice — {minutes} min on {task}")
        texts.addWidget(self.title_label)
        self.score_label = _set_font(Caption(self._score_line(counts), content), 12, 400, tabular=True)
        self.score_label.setWordWrap(True)
        texts.addWidget(self.score_label)
        top.addLayout(texts, 1)
        v.addLayout(top)

        row = QHBoxLayout()
        row.setContentsMargins(44, 0, 0, 0)
        row.setSpacing(6)
        self.main_button = Button("", "primary", size="sm", parent=content)
        self.keep_button = Button(f"Keep going +{KEEP_GOING_MINUTES}", "secondary", size="sm", parent=content)
        self.done_button = Button("Done", "ghost", size="sm", parent=content)
        row.addWidget(self.main_button)
        row.addWidget(self.keep_button)
        row.addStretch(1)
        row.addWidget(self.done_button)
        v.addLayout(row)
        self.set_content(content)

        self.main_button.clicked.connect(self._on_primary)
        self.keep_button.clicked.connect(self.keep_going)
        self.done_button.clicked.connect(lambda: self.dismiss())
        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.setInterval(PANEL_AUTO_HIDE_MS)
        self._auto_hide.timeout.connect(lambda: self.dismiss())
        self._check_timer = _one_shot(self, self.check.play)
        self._hover_left_ms: int | None = None
        self.hover_changed.connect(self._on_hover)
        self.closed.connect(self._on_closed)
        ctx.engine.state_changed.connect(self._on_engine_state)
        self._sync_actions()

    # ------------------------------------------------------------------ text / state
    def _score_line(self, counts: dict[str, int]) -> str:
        session = self._session
        parts: list[str] = []
        if session is not None and self.outcome != "completed":
            parts.append(f"Ended early · {minutes_summary(session)}")
        score, unanswered = score_summary(counts)
        parts.extend(x for x in (score, unanswered) if x)
        engine = self._ctx.engine
        if str(engine.state) == "break":  # auto-started break
            parts.append(f"{int(round(engine.break_total_seconds / 60.0))}-min break started")
        if not parts:
            planned = int(round(session.planned_seconds / 60.0)) if session is not None else 0
            parts.append(f"Your full {planned}-minute block" if planned else "Session complete")
        return " · ".join(parts)

    def breaks_offered(self) -> bool:
        """True when the primary action is "Take a N-min break"."""
        return bool(self._ctx.settings.get("breaks_enabled"))

    def _on_engine_state(self, _state: str) -> None:
        self._sync_actions()

    def _sync_actions(self) -> None:
        state = str(self._ctx.engine.state)
        idle = state == "idle"
        minutes = int(self._ctx.settings.get("break_minutes"))
        self.main_button.setText(f"Take a {minutes}-min break" if self.breaks_offered() else "Plan next")
        self.main_button.setVisible(idle)
        self.keep_button.setVisible(self._session is not None and state in ("idle", "break"))
        self.done_button.set_variant("ghost" if idle else "secondary")
        self.disable_child_focus()

    # ------------------------------------------------------------------ presenting
    def present(self, priority: int | None = None) -> None:
        """Push to the popup stack (priority 0) and start the 120 s auto-hide."""
        super().present(PRIORITIES["completion"] if priority is None else priority)
        self._auto_hide.start()
        self._check_timer.start(160 if animations.motion_enabled() else 0)

    def auto_hide_remaining_ms(self) -> int:
        """Milliseconds until the panel hides itself (-1 when not counting)."""
        return self._auto_hide.remainingTime() if self._auto_hide.isActive() else -1

    def _on_hover(self, inside: bool) -> None:
        if inside:
            if self._auto_hide.isActive():
                self._hover_left_ms = self._auto_hide.remainingTime()
                self._auto_hide.stop()
        elif self._hover_left_ms is not None:
            self._auto_hide.start(max(_MIN_HOVER_RESUME_MS, self._hover_left_ms))
            self._hover_left_ms = None

    def dismiss(self, animate: bool = True) -> None:
        """Animate away (the registry forgets the panel at once)."""
        self._forget()
        super().dismiss(animate)

    def _forget(self) -> None:
        self._auto_hide.stop()
        if _PANELS.get(self._ctx) is self:
            del _PANELS[self._ctx]

    def _on_closed(self) -> None:
        self._forget()
        try:
            self._ctx.engine.state_changed.disconnect(self._on_engine_state)
        except (RuntimeError, TypeError):
            pass
        # Each completion gets a new panel: free this one (its button/timer lambdas reference it
        # from the C++ side, so it would otherwise live on as a hidden top-level window).
        self.deleteLater()

    # ------------------------------------------------------------------ actions
    def _on_primary(self) -> None:
        if self.breaks_offered():
            self.dismiss()
            self._ctx.engine.start_break(int(self._ctx.settings.get("break_minutes")))
        else:
            self.plan_next()

    def keep_going(self) -> None:
        """Start another 15 minutes on the same task (clears the pending sheet)."""
        session = self._session
        self.dismiss()
        if session is None:
            return
        try:
            if self._ctx.repo.kv_get(PENDING_KEY) == self.session_id:
                self._ctx.repo.kv_set(PENDING_KEY, None)
        except sqlite3.Error:
            log.debug("pending kv failed", exc_info=True)
        self._ctx.engine.start(session.task, KEEP_GOING_MINUTES, session.project, session.planned_task_id)

    def plan_next(self) -> None:
        """Open Madhura on the Focus page and focus the task field."""
        self.dismiss()
        window = self._ctx.window
        if window is not None:
            window.show_main("focus", activate=True)
            window.focus_task_input()


# =========================================================================== pending hooks


class _PendingWatcher(QObject):
    """Shows the pending sheet when the main window is shown again or activated."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx.events)
        self._ctx_ref = weakref.ref(ctx)
        self._window: QObject | None = None
        self._defer = QTimer(self)
        self._defer.setSingleShot(True)
        self._defer.setInterval(0)
        self._defer.timeout.connect(self._show)
        ctx.events.main_visibility_changed.connect(self._on_visible)
        self.attach_window(ctx.window)

    def attach_window(self, window: Any) -> None:
        """Watch *window* for activation (idempotent)."""
        if window is self._window or not isinstance(window, QObject):
            return
        if self._window is not None:
            try:
                self._window.removeEventFilter(self)
            except RuntimeError:
                pass
        self._window = window
        window.installEventFilter(self)

    def _on_visible(self, visible: bool) -> None:
        if visible:
            self._defer.start()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._window and event.type() == QEvent.Type.WindowActivate:
            self._defer.start()
        return False

    def _show(self) -> None:
        ctx = self._ctx_ref()
        if ctx is not None and not ctx.repo.closed:
            show_pending_complete(ctx)


def _ensure_hooks(ctx: AppContext) -> None:
    watcher = _WATCHERS.get(ctx)
    if watcher is None:
        _WATCHERS[ctx] = _PendingWatcher(ctx)
    else:
        watcher.attach_window(ctx.window)


def _forget_sheet(ctx: AppContext, sheet: SessionCompleteSheet) -> None:
    sheets = _SHEETS.get(ctx)
    if sheets is not None and sheets.get(sheet.session_id) is sheet:
        del sheets[sheet.session_id]


def _show_sheet(ctx: AppContext, session_id: int, outcome: str, *, pending: bool = False) -> SessionCompleteSheet | None:
    window = ctx.window
    if window is None:
        return None
    sheets = _SHEETS.setdefault(ctx, {})
    existing = sheets.get(session_id)
    if existing is not None:
        return existing
    panel = _PANELS.get(ctx)
    if panel is not None and panel.session_id == session_id:
        panel.dismiss()
    sheet = SessionCompleteSheet(ctx, session_id, outcome, pending=pending)
    sheets[session_id] = sheet
    window.show_sheet(sheet)
    return sheet


def _show_panel(ctx: AppContext, session_id: int, outcome: str) -> CompletionPanel:
    old = _PANELS.pop(ctx, None)
    if old is not None:
        old.dismiss(animate=False)
    panel = CompletionPanel(ctx, session_id, outcome)
    _PANELS[ctx] = panel
    panel.present()
    return panel


# =========================================================================== entry points


def present_session_complete(ctx: AppContext, session_id: int, outcome: str, restored: bool) -> None:
    """React to ``engine.session_finished(id, outcome, restored)`` (see module docstring)."""
    _ensure_hooks(ctx)
    outcome = str(outcome)
    session: Session | None
    try:
        session = ctx.repo.get_session(int(session_id))
    except sqlite3.Error:
        log.exception("Could not load session %s", session_id)
        session = None
    if restored:
        _toast(ctx, restored_text(session.task if session is not None else ""), "info", 6000)
        return
    if outcome == "abandoned":
        _toast(ctx, "Too short to save" if session is None else "Session discarded", "info")
        return
    if outcome not in COUNTED or session is None:
        return
    ctx.sound.play("complete")
    window = ctx.window
    if window is not None and window.is_main_active():
        _show_sheet(ctx, int(session_id), outcome)
        return
    _show_panel(ctx, int(session_id), outcome)
    try:
        ctx.repo.kv_set(PENDING_KEY, int(session_id))
    except sqlite3.Error:
        log.exception("Could not remember the pending session summary")


def show_pending_complete(ctx: AppContext) -> SessionCompleteSheet | None:
    """Show the sheet for kv 'pending_complete' if the main window is visible (idempotent).

    Called after startup and whenever the main window is shown or activated. Returns the sheet
    (or None when nothing is pending).
    """
    _ensure_hooks(ctx)
    window = ctx.window
    if window is None or not window.is_main_visible():
        return None
    repo = ctx.repo
    try:
        pending = repo.kv_get(PENDING_KEY)
    except sqlite3.Error:
        log.exception("Could not read the pending session summary")
        return None
    if pending is None:
        return None
    if isinstance(pending, bool) or not isinstance(pending, int):
        repo.kv_set(PENDING_KEY, None)
        return None
    try:
        session = repo.get_session(pending)
    except sqlite3.Error:
        log.exception("Could not load the pending session")
        return None
    if session is None or str(session.outcome) not in COUNTED:
        repo.kv_set(PENDING_KEY, None)
        return None
    return _show_sheet(ctx, pending, str(session.outcome), pending=True)
