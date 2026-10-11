"""NudgeService — planning nudges between blocks (spec §1.7, §5.3).

Eligible when ``nudge_mode`` is not "off" and: the engine is IDLE (or PAUSED for ≥ 20 min →
"Still paused on <task>"), the time is inside work hours, nudges are neither snoozed
(kv 'nudge_snooze_until') nor done for today (kv 'nudge_done_date' = logical date), attention is
"available", and ``nudge_idle_minutes`` have passed since becoming idle (session/break end, app
start, or input resuming after away). Repeats every ``nudge_idle_minutes`` while still eligible.

* gentle → :class:`NudgePopup` (lowest stack priority, never alongside another panel, 30 s
  auto-hide). The first nudge of a logical day with a planned task reads "Good morning — your
  first block: <task>".
* firm → ``window.show_main('focus', activate=False)`` (raise without focus) + the Focus page's
  nudge banner, gated on input idle ≥ 2 s so it never lands mid-keystroke. When the user is
  already in Madhura (active window) only the banner is set — the page they are on is never
  switched — and an open sheet in an inactive window postpones the raise to the next round.

Once per logical day, at the first eligible-idle moment after ``work_end``, a gentle "Wrap up your
day?" panel opens the WrapUpSheet. Evaluation runs on a single-shot timer (≤ 30 s) and on every
relevant signal; tests call :meth:`check` after moving the FakeClock.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from madhura.core.days import is_work_day
from madhura.core.models import PlannedTask, Session, from_iso, to_date_str, to_iso
from madhura.core.nudges import in_work_hours, work_window
from madhura.core.timefmt import fmt_preset

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.ui.overlays.nudge_popup import NudgePopup

__all__ = [
    "NudgeService",
    "SNOOZE_KEY",
    "DONE_KEY",
    "LAST_NUDGE_DATE_KEY",
    "WRAP_UP_KEY",
    "PAUSED_NUDGE_SECONDS",
    "CONTINUE_WINDOW_SECONDS",
    "FIRM_INPUT_IDLE_SECONDS",
]

log = logging.getLogger(__name__)

SNOOZE_KEY = "nudge_snooze_until"
DONE_KEY = "nudge_done_date"
LAST_NUDGE_DATE_KEY = "nudge_last_date"
WRAP_UP_KEY = "wrap_up_prompt_date"
PAUSED_NUDGE_SECONDS = 20 * 60
CONTINUE_WINDOW_SECONDS = 2 * 3600
FIRM_INPUT_IDLE_SECONDS = 2.0
FIRM_RETRY_MS = 1000
MAX_CHECK_MS = 30_000
BUSY_STACK_RETRY_MS = 15_000


def _quote(text: str, limit: int = 32) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"‘{text}’"


class NudgeService(QObject):
    """Nudges the user to plan the next block (``ctx.nudges``); see the module docstring.

    ``state_changed`` fires when snooze / done-for-today / the firm banner change. Extension:
    ``nudged(variant)`` ("next" | "morning" | "paused" | "wrap_up") whenever a nudge is delivered.
    """

    state_changed = Signal()
    nudged = Signal(str)

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._idle_since = ctx.clock.time()
        self._last_nudge: float | None = None
        self._banner = False
        self._panel: NudgePopup | None = None
        self._attention = ctx.attention.state()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.check)
        self._firm_retry = QTimer(self)
        self._firm_retry.setSingleShot(True)
        self._firm_retry.setInterval(FIRM_RETRY_MS)
        self._firm_retry.timeout.connect(self.check)
        ctx.engine.state_changed.connect(self._on_engine_state)
        ctx.attention.changed.connect(self._on_attention)
        ctx.settings.changed.connect(self._on_setting)
        ctx.events.day_changed.connect(self.check)
        self._arm(self._grace_seconds() * 1000)

    # ================================================================== state
    @property
    def snoozed_until(self) -> datetime | None:
        """End of the current snooze, or None."""
        try:
            until = from_iso(self._ctx.repo.kv_get(SNOOZE_KEY))
        except (TypeError, ValueError, sqlite3.Error):
            return None
        if until is None or until <= self._ctx.clock.now():
            return None
        return until

    @property
    def is_done_for_today(self) -> bool:
        """True after "Done for today" on the current logical day."""
        try:
            return self._ctx.repo.kv_get(DONE_KEY) == to_date_str(self._ctx.today())
        except sqlite3.Error:
            return False

    @property
    def banner_visible(self) -> bool:
        """True while the firm-nudge banner is requested on the Focus page."""
        return self._banner

    @property
    def panel(self) -> NudgePopup | None:
        """The gentle panel while it is shown or queued (None once it starts closing)."""
        return self._live_panel()

    def _live_panel(self) -> NudgePopup | None:
        from madhura.ui.widgets.popup import PopupStack

        panel = self._panel
        if panel is None:
            return None
        stack = PopupStack.instance_if_exists()
        try:
            alive = stack is not None and stack.contains(panel)
        except RuntimeError:
            alive = False
        if not alive:
            self._panel = None
            return None
        return panel

    @property
    def idle_since(self) -> float:
        """Epoch the current idle grace counts from."""
        return self._idle_since

    # ================================================================== commands
    def snooze(self, minutes: int) -> None:
        """Snooze nudges for *minutes*."""
        until = (self._ctx.clock.now() + timedelta(minutes=int(minutes))).replace(microsecond=0)
        self._ctx.repo.kv_set(SNOOZE_KEY, to_iso(until))
        self._clear_ui()
        self.state_changed.emit()
        self._arm()

    def done_for_today(self) -> None:
        """Stop nudging until the next logical day."""
        self._ctx.repo.kv_set(DONE_KEY, to_date_str(self._ctx.today()))
        self._clear_ui(include_wrap_up=False)
        self.state_changed.emit()
        self._arm()

    def resume(self) -> None:
        """Clear snooze and done-for-today (the next nudge waits one idle grace period)."""
        self._ctx.repo.kv_set(SNOOZE_KEY, None)
        self._ctx.repo.kv_set(DONE_KEY, None)
        self._last_nudge = self._ctx.clock.time()
        self.state_changed.emit()
        self._arm()

    def start_meeting(self, minutes: int) -> None:
        """"In a meeting…": run a 'Meeting' block of *minutes* (30 / 60)."""
        self._clear_ui()
        self._ctx.engine.start_break(int(minutes), "Meeting")

    def resume_session(self) -> None:
        """Paused-nudge primary: resume the paused session."""
        self._clear_ui()
        self._ctx.engine.resume()

    def open_wrap_up(self) -> None:
        """Show the main window on Focus and open the WrapUpSheet."""
        from madhura.ui.overlays.wrap_up import open_wrap_up

        open_wrap_up(self._ctx)

    # ================================================================== primary action
    def first_task(self) -> PlannedTask | None:
        """First unfinished Up next task of the logical day (by position)."""
        try:
            tasks = self._ctx.repo.tasks_for_date(self._ctx.today(), include_done=False)
        except sqlite3.Error:
            log.exception("Could not read Up next")
            return None
        return tasks[0] if tasks else None

    def last_session(self) -> Session | None:
        """Most recent counted session (completed / ended early)."""
        try:
            rows = self._ctx.repo.recent_sessions(limit=1, include_abandoned=False)
        except sqlite3.Error:
            log.exception("Could not read recent sessions")
            return None
        return rows[0] if rows else None

    def primary_parts(self) -> tuple[str, str | None, str, Callable[[], None]]:
        """(verb, task title or None, suffix, callback) of the one-click primary action.

        Paused → ("Resume", None, "", …); Up next has items → ("Start", <first task>, "25m", …);
        the last session ended < 2 h ago → ("Continue", <last task>, "", …); else
        ("Plan now", None, "", …). The popup uses the parts to elide the title by pixel width.
        """
        engine = self._ctx.engine
        if str(engine.state) == "paused":
            return "Resume", None, "", self.resume_session
        task = self.first_task()
        if task is not None:
            tid = int(task.id)
            return "Start", task.title, fmt_preset(int(task.minutes)), lambda: self._start_task(tid)
        last = self.last_session()
        if last is not None and last.ended_at is not None:
            age = (self._ctx.clock.now() - last.ended_at).total_seconds()
            if 0 <= age < CONTINUE_WINDOW_SECONDS:
                sid = int(last.id)
                return "Continue", last.task, "", lambda: self._continue(sid)
        return "Plan now", None, "", self._plan_now

    def primary_action(self) -> tuple[str, Callable[[], None]]:
        """Label + callback of the one-click primary action.

        Paused → "Resume"; Up next has items → "Start ‘<first task>’ · 25m"; the last session
        ended < 2 h ago → "Continue ‘<last task>’"; else "Plan now".
        """
        verb, title, suffix, callback = self.primary_parts()
        if title is None:
            return verb, callback
        label = f"{verb} {_quote(title)}"
        if suffix:
            label += f" · {suffix}"
        return label, callback

    def _start_task(self, task_id: int) -> None:
        ctx = self._ctx
        try:
            task = ctx.repo.get_task(task_id)
        except sqlite3.Error:
            task = None
        if task is None or task.done:
            self._plan_now()
            return
        self._clear_ui()
        if ctx.engine.start(task.title, int(task.minutes), task.project, planned_task_id=int(task.id)) is None:
            self._plan_now()

    def _continue(self, session_id: int) -> None:
        ctx = self._ctx
        try:
            last = ctx.repo.get_session(session_id)
        except sqlite3.Error:
            last = None
        if last is None:
            self._plan_now()
            return
        planned_task_id = last.planned_task_id
        if planned_task_id is not None:
            try:
                linked = ctx.repo.get_task(planned_task_id)
            except sqlite3.Error:
                linked = None
            if linked is None or linked.done:
                planned_task_id = None
        minutes = max(5, int(round(last.planned_seconds / 60.0)))
        self._clear_ui()
        if ctx.engine.start(last.task, minutes, last.project, planned_task_id=planned_task_id) is None:
            self._plan_now()

    def _plan_now(self) -> None:
        self._clear_ui()
        self._last_nudge = self._ctx.clock.time()
        window = self._ctx.window
        if window is not None:
            window.show_main("focus", activate=True)
            window.focus_task_input()

    # ================================================================== eligibility
    def _grace_seconds(self) -> float:
        return float(self._ctx.settings.get("nudge_idle_minutes")) * 60.0

    def _in_work_hours(self, now: datetime) -> bool:
        s = self._ctx.settings
        try:
            return in_work_hours(now, int(s.get("work_days")), s.get("work_start"), s.get("work_end"))
        except ValueError:
            log.warning("Invalid work hours in settings")
            return False

    def _pause_epoch(self) -> float | None:
        since = self._ctx.engine.paused_since
        if since is None:
            return None
        return self._ctx.clock.time() - max(0.0, (self._ctx.clock.now() - since).total_seconds())

    def eligible_variant(self) -> str | None:
        """"next" | "morning" | "paused" when a nudge may be shown now (grace aside), else None."""
        ctx = self._ctx
        if ctx.settings.get("nudge_mode") == "off":
            return None
        if self.is_done_for_today or self.snoozed_until is not None:
            return None
        if not self._in_work_hours(ctx.clock.now()):
            return None
        if ctx.attention.state() != "available":
            return None
        state = str(ctx.engine.state)
        if state == "idle":
            return "morning" if self._is_first_of_day() else "next"
        if state == "paused":
            since = self._pause_epoch()
            if since is not None and ctx.clock.time() - since >= PAUSED_NUDGE_SECONDS:
                return "paused"
        return None

    def _is_first_of_day(self) -> bool:
        ctx = self._ctx
        today = ctx.today()
        try:
            if ctx.repo.kv_get(LAST_NUDGE_DATE_KEY) == to_date_str(today):
                return False
            if self.first_task() is None:
                return False
            start, end = ctx.day_bounds(today)
            return not ctx.repo.sessions_between(start, end)
        except sqlite3.Error:
            return False

    def due_at(self, variant: str) -> float:
        """Epoch at which a nudge of *variant* becomes due (idle grace + repeat cadence)."""
        grace = self._grace_seconds()
        if variant == "paused":
            since = self._pause_epoch()
            base = (since if since is not None else self._ctx.clock.time()) + PAUSED_NUDGE_SECONDS
        else:
            base = self._idle_since + grace
        if self._last_nudge is not None:
            base = max(base, self._last_nudge + grace)
        return base

    # ================================================================== wrap-up prompt
    def _last_work_end(self, now: datetime) -> datetime | None:
        """End of the most recent work window that closed during today's logical day."""
        ctx = self._ctx
        s = ctx.settings
        start, end = s.get("work_start"), s.get("work_end")
        if start == end:
            return None
        today = ctx.today()
        day_start, _day_end = ctx.day_bounds(today)
        mask = int(s.get("work_days"))
        best: datetime | None = None
        for d in (today - timedelta(days=1), today):
            if not is_work_day(d, mask):
                continue
            try:
                _begin, finish = work_window(d, start, end)
            except ValueError:
                return None
            if day_start <= finish <= now and (best is None or finish > best):
                best = finish
        return best

    def wrap_up_due(self) -> bool:
        """True at the first eligible-idle moment after work_end (once per logical day)."""
        ctx = self._ctx
        if ctx.settings.get("nudge_mode") == "off":
            return False
        try:
            if ctx.repo.kv_get(WRAP_UP_KEY) == to_date_str(ctx.today()):
                return False
        except sqlite3.Error:
            return False
        if self.is_done_for_today or self.snoozed_until is not None:
            return False
        if str(ctx.engine.state) != "idle" or ctx.attention.state() != "available":
            return False
        now = ctx.clock.now()
        if self._in_work_hours(now):
            return False
        if ctx.clock.time() < self._idle_since + self._grace_seconds():
            return False
        end = self._last_work_end(now)
        return end is not None and now >= end

    # ================================================================== evaluation
    def check(self) -> None:
        """Evaluate eligibility now and deliver a nudge (or the wrap-up prompt) when due."""
        ctx = self._ctx
        if ctx.repo.closed:
            return
        if ctx.settings.get("nudge_mode") == "off":
            self._clear_ui()
            return
        variant = self.eligible_variant()
        self._reconcile_ui(variant)
        if variant is None:
            if self.wrap_up_due() and not self._stack_busy() and self._live_panel() is None:
                self._deliver_wrap_up()
            self._arm()
            return
        now = ctx.clock.time()
        due = self.due_at(variant)
        if now < due:
            self._arm((due - now) * 1000)
            return
        if self._live_panel() is not None:
            self._arm()
            return
        mode = ctx.settings.get("nudge_mode")
        if mode == "firm" and ctx.window is not None:
            in_window = self._window_active()
            if (in_window and self._banner) or (not in_window and self._window_has_sheet()):
                # Already waiting on Focus for a user who is in Madhura; or a sheet is open:
                # don't navigate underneath it. Try again next round.
                self._arm()
                return
            idle = ctx.attention.input_idle_seconds()
            if idle is not None and idle < FIRM_INPUT_IDLE_SECONDS:
                self._firm_retry.start()
                return
            self._deliver_firm(variant, raise_window=not in_window)
        else:
            if self._stack_busy():
                self._arm(BUSY_STACK_RETRY_MS)
                return
            self._deliver_gentle(variant)
        self._after_delivery(variant)

    def _after_delivery(self, variant: str) -> None:
        ctx = self._ctx
        self._last_nudge = ctx.clock.time()
        try:
            ctx.repo.kv_set(LAST_NUDGE_DATE_KEY, to_date_str(ctx.today()))
        except sqlite3.Error:
            log.debug("nudge date kv failed", exc_info=True)
        try:
            ctx.sound.play("nudge")
        except Exception:  # noqa: BLE001 - sound must never break a nudge
            log.debug("nudge sound failed", exc_info=True)
        self.nudged.emit(variant)
        self._arm()

    def _deliver_gentle(self, variant: str) -> None:
        from madhura.ui.overlays.nudge_popup import NudgePopup

        panel = NudgePopup(self._ctx, self, variant)
        panel.closed.connect(lambda p=panel: self._on_panel_closed(p))
        self._panel = panel
        panel.present()

    def _deliver_firm(self, variant: str, raise_window: bool = True) -> None:
        """Set the Focus page banner; *raise_window* also brings the window up on Focus (never
        while the user is in Madhura — the page they are on stays)."""
        window = self._ctx.window
        if window is None:
            return
        if raise_window:
            window.show_main("focus", activate=False)
        window.set_nudge_banner(True)
        if not self._banner:
            self._banner = True
            self.state_changed.emit()

    def _deliver_wrap_up(self) -> None:
        ctx = self._ctx
        try:
            ctx.repo.kv_set(WRAP_UP_KEY, to_date_str(ctx.today()))
        except sqlite3.Error:
            log.debug("wrap-up kv failed", exc_info=True)
            return
        self._deliver_gentle("wrap_up")
        try:
            ctx.sound.play("nudge")
        except Exception:  # noqa: BLE001
            log.debug("nudge sound failed", exc_info=True)
        self.nudged.emit("wrap_up")

    def _window_active(self) -> bool:
        """The user is in Madhura (active main window): never raise it or switch its page."""
        window = self._ctx.window
        try:
            return bool(window is not None and window.is_main_active())
        except RuntimeError:
            return False

    def _window_has_sheet(self) -> bool:
        window = self._ctx.window
        try:
            return bool(window is not None and window.has_sheet())
        except RuntimeError:
            return False

    def _stack_busy(self) -> bool:
        from madhura.ui.widgets.popup import PopupStack

        stack = PopupStack.instance_if_exists()
        if stack is None:
            return False
        return any(p is not self._panel for p in stack.visible_panels())

    # ================================================================== UI bookkeeping
    def _reconcile_ui(self, variant: str | None) -> None:
        """Close a panel / banner that no longer matches the current eligibility."""
        panel = self._live_panel()
        if panel is not None:
            pv = panel.variant
            if pv == "wrap_up":
                stale = str(self._ctx.engine.state) != "idle"
            elif variant is None:
                stale = True
            else:
                stale = (pv == "paused") != (variant == "paused")
            if stale:
                self._dismiss_panel()
        if self._banner and variant is None:
            self._hide_banner()

    def _clear_ui(self, include_wrap_up: bool = True) -> None:
        panel = self._live_panel()
        if panel is not None and (include_wrap_up or panel.variant != "wrap_up"):
            self._dismiss_panel()
        self._firm_retry.stop()
        self._hide_banner()

    def _dismiss_panel(self) -> None:
        panel = self._panel
        self._panel = None
        if panel is not None:
            try:
                panel.dismiss()
            except RuntimeError:
                pass

    def _hide_banner(self) -> None:
        if not self._banner:
            return
        self._banner = False
        window = self._ctx.window
        if window is not None:
            try:
                window.set_nudge_banner(False)
            except RuntimeError:
                pass
        self.state_changed.emit()

    def _on_panel_closed(self, panel: NudgePopup) -> None:
        if self._panel is panel:
            self._panel = None
        panel.deleteLater()

    def _arm(self, ms: float | None = None) -> None:
        wait = MAX_CHECK_MS if ms is None else int(max(500.0, min(float(MAX_CHECK_MS), float(ms))))
        self._timer.start(wait)

    # ================================================================== signals
    def _on_engine_state(self, state: str) -> None:
        state = str(state)
        if state == "idle":
            self._idle_since = self._ctx.clock.time()
        if state in ("focus", "break"):
            self._clear_ui()
        self.check()

    def _on_attention(self, state: str) -> None:
        state = str(state)
        if self._attention == "away" and state != "away":
            # Input resumed after being away: the idle grace starts again.
            self._idle_since = max(self._idle_since, self._ctx.clock.time())
        self._attention = state
        self.check()

    def _on_setting(self, key: str) -> None:
        if key in ("nudge_mode", "nudge_idle_minutes", "work_days", "work_start", "work_end", "day_start_hour"):
            if key == "nudge_mode" and self._ctx.settings.get("nudge_mode") != "firm":
                self._hide_banner()
            self.check()
