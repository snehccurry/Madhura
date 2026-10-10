"""HabitReminderService — timed habit reminders and focus-time auto-complete (spec §1.8).

Runs on the main thread: a 30 s :class:`QTimer` re-evaluates every habit with
:func:`madhura.core.habits.due_status` (scheduled today, has a time, time reached, reminder on, no
log today, not snoozed, not satisfied by focus time).

* **Lateness cap** — a reminder first noticed more than 120 min late (app launched late, machine
  asleep) is never shown that day; the habits page shows it as pending instead.
* **Holding** — while the engine is in FOCUS, non-urgent reminders are held and delivered together
  when the session ends, is paused or a break starts. While attention is busy (presenting), quiet
  or away, *all* reminders are held (urgent only bypasses the focus hold) and delivered when
  attention is available again; an open panel is taken down (no snooze) when that happens.
* **Panel** — one :class:`HabitReminderPopup` lists up to 3 habits (Done / Snooze 15m / Skip
  today). Closing it snoozes every listed habit for 15 min. After 3 snoozes of a habit in a day it
  is not reminded again that day. Snoozes persist in kv ``'habit_snoozes'`` as
  ``{habit_id: {until, count, date}}``. Sound ``'habit'`` once per delivery.
* **Auto-complete** — on ``session_finished`` (completed / ended early, also restored) and on
  ``day_changed``, habits with ``focus_minutes`` get 'done' for the day when that logical day's
  counted focus minutes (filtered by the habit's project, case-insensitive) reach the target.

Records go through ``repo.set_habit_status`` followed by ``events.data_changed('habits')``.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from madhura.core import habits as hcore
from madhura.core.days import logical_date
from madhura.core.models import COUNTED_OUTCOMES, Habit, from_date_str, from_iso, to_date_str, to_iso
from madhura.core.timefmt import fmt_time

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.ui.overlays.habit_reminder_popup import HabitReminderPopup, ReminderItem

__all__ = ["HabitReminderService", "SNOOZES_KEY", "CHECK_INTERVAL_MS", "HOLD_ATTENTION"]

log = logging.getLogger(__name__)

SNOOZES_KEY = "habit_snoozes"
CHECK_INTERVAL_MS = 30_000
STARTUP_DELAY_MS = 1_500
#: Attention states that hold every reminder back.
HOLD_ATTENTION = ("busy", "quiet", "away")


class HabitReminderService(QObject):
    """Reminds about due habits and auto-completes focus habits (``ctx.habit_reminders``)."""

    #: Emitted whenever the held/shown sets change (tests, diagnostics).
    reminders_changed = Signal()

    def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._day: date = ctx.today()
        #: habit id → logical day, waiting to be shown (focus / attention hold), in due order.
        self._held: dict[int, date] = {}
        #: habit id → logical day, listed in the panel.
        self._shown: dict[int, date] = {}
        #: habit id → True when suppressed today by the lateness cap (or edited after its time).
        self._first_seen: dict[int, bool] = {}
        self._signatures: dict[int, tuple[Any, ...]] = {}
        self._signatures_ready = False
        self._snoozes: dict[int, dict[str, Any]] = self._load_snoozes()
        self._prune_snoozes(self._day)
        self._closing_quietly = False
        self.popup: HabitReminderPopup | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()
        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.timeout.connect(self.check)
        self._startup = QTimer(self)
        self._startup.setSingleShot(True)
        self._startup.timeout.connect(self._on_startup)
        self._startup.start(STARTUP_DELAY_MS)

        engine = ctx.engine
        engine.state_changed.connect(self._on_engine_state)
        engine.session_finished.connect(self._on_session_finished)
        ctx.events.day_changed.connect(self._on_day_changed)
        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.attention.changed.connect(self._on_attention)

    # ================================================================== public API

    @property
    def queued_count(self) -> int:
        """Reminders held back (during focus or while attention is busy/quiet/away)."""
        return len(self._held)

    def held_ids(self) -> list[int]:
        """Ids of held reminders, in delivery order."""
        return list(self._held)

    def shown_ids(self) -> list[int]:
        """Ids listed in the reminder panel."""
        return list(self._shown)

    def is_suppressed(self, habit_id: int) -> bool:
        """True when the habit won't be reminded today (lateness cap / edited after its time)."""
        return bool(self._first_seen.get(int(habit_id)))

    def snooze_info(self, habit_id: int) -> dict[str, Any] | None:
        """``{"until": datetime, "count": int, "date": date}`` for today, or None."""
        entry = self._snoozes.get(int(habit_id))
        if entry is None or entry["date"] != self._ctx.today():
            return None
        return dict(entry)

    def stop(self) -> None:
        """Stop the timers (shutdown)."""
        self._timer.stop()
        self._soon.stop()
        self._startup.stop()

    def reset(self) -> None:
        """Forget held reminders, snoozes and per-day state, and close the panel (data reset)."""
        self._held.clear()
        self._shown.clear()
        self._first_seen.clear()
        self._signatures.clear()
        self._signatures_ready = False
        self._snoozes = {}
        self._day = self._ctx.today()
        try:
            if not self._ctx.repo.closed:
                self._ctx.repo.kv_set(SNOOZES_KEY, None)
        except sqlite3.Error:
            log.exception("Could not clear habit snoozes")
        self._close_popup_quietly()
        self.reminders_changed.emit()

    def check(self) -> list[int]:
        """Evaluate every habit now; returns the ids that newly became due (held or shown)."""
        ctx = self._ctx
        if ctx.repo.closed:
            return []
        try:
            return self._check()
        except sqlite3.Error:
            log.exception("Habit reminder check failed")
            return []

    def mark(self, habit_id: int, status: str | None, day: date | None = None) -> None:
        """Record *status* ('done' / 'skipped' / None) for *habit_id* on *day* (default today)."""
        ctx = self._ctx
        hid = int(habit_id)
        d = day or self._shown.get(hid) or self._held.get(hid) or ctx.today()
        ctx.repo.set_habit_status(hid, d, status, at=ctx.clock.now().replace(microsecond=0))
        self._shown.pop(hid, None)
        self._held.pop(hid, None)
        ctx.events.data_changed.emit("habits")

    def snooze(self, habit_id: int, minutes: int = hcore.SNOOZE_MINUTES) -> None:
        """Snooze *habit_id* for *minutes* (counts toward the 3-per-day cap; persisted)."""
        ctx = self._ctx
        hid = int(habit_id)
        today = ctx.today()
        now = ctx.clock.now().replace(microsecond=0)
        entry = self._snoozes.get(hid)
        if entry is None or entry["date"] != today:
            entry = {"until": now, "count": 0, "date": today}
        entry["count"] = int(entry["count"]) + 1
        entry["until"] = now + timedelta(minutes=int(minutes))
        self._snoozes[hid] = entry
        self._save_snoozes()
        self._shown.pop(hid, None)
        self._held.pop(hid, None)
        self.reminders_changed.emit()

    def auto_complete(self, d: date | None = None) -> list[int]:
        """Mark focus habits done for logical date *d* (default today) when their focus-minute
        target is met. Returns the ids marked; emits ``data_changed('habits')`` if any."""
        ctx = self._ctx
        if ctx.repo.closed:
            return []
        d = d or ctx.today()
        try:
            habits = [h for h in ctx.repo.habits() if h.is_auto and hcore.is_scheduled(h, d)]
            if not habits:
                return []
            logged = {lg.habit_id for lg in ctx.repo.habit_logs_for_date(d)}
            start, end = ctx.day_bounds(d)
            sessions = ctx.repo.sessions_between(start, end)
            now = ctx.clock.now().replace(microsecond=0)
            marked: list[int] = []
            for h in habits:
                created = hcore.created_date(h)
                if h.id in logged or (created is not None and d < created - timedelta(days=1)):
                    continue
                if hcore.auto_satisfied(h, hcore.focused_minutes(sessions, h.project)):
                    ctx.repo.set_habit_status(h.id, d, "done", at=now)
                    marked.append(h.id)
        except sqlite3.Error:
            log.exception("Habit auto-complete failed")
            return []
        if marked:
            for hid in marked:
                self._shown.pop(hid, None)
                self._held.pop(hid, None)
            ctx.events.data_changed.emit("habits")
        return marked

    # ================================================================== evaluation

    def _check(self) -> list[int]:
        ctx = self._ctx
        now = ctx.clock.now()
        today = ctx.today()
        dsh = ctx.day_start_hour()
        self._roll_day(today)
        habits = {h.id: h for h in ctx.repo.habits()}
        logs = {lg.habit_id: lg for lg in ctx.repo.habit_logs_for_date(today)}
        self._track_signatures(habits, now, today, dsh)
        auto_done = self._auto_done(habits.values(), today)
        new: list[int] = []
        ordered = sorted(habits.values(), key=lambda h: hcore.sort_key(h, day_start_hour=dsh))
        for h in ordered:
            snooze = self._snoozes.get(h.id)
            if snooze is not None and snooze["date"] != today:
                snooze = None
            label = hcore.due_status(
                h,
                now,
                logs.get(h.id),
                snooze["until"] if snooze else None,
                day_start_hour=dsh,
                auto_done=h.id in auto_done,
            )
            capped = snooze is not None and int(snooze["count"]) >= hcore.MAX_SNOOZES_PER_DAY
            if label is None or capped:
                self._shown.pop(h.id, None)
                self._held.pop(h.id, None)
                continue
            if h.id not in self._first_seen:
                late = hcore.minutes_late(h, now, day_start_hour=dsh)
                self._first_seen[h.id] = late > hcore.LATENESS_CAP_MINUTES
            if self._first_seen[h.id] or h.id in self._shown or h.id in self._held:
                continue
            self._held[h.id] = today
            new.append(h.id)
        for hid in [i for i in self._held if i not in habits]:
            del self._held[hid]
        for hid in [i for i in self._shown if i not in habits]:
            del self._shown[hid]
        self._deliver(habits, now)
        if new:
            self.reminders_changed.emit()
        return new

    def _roll_day(self, today: date) -> None:
        if today == self._day:
            return
        self._day = today
        self._first_seen.clear()
        self._held = {k: v for k, v in self._held.items() if v == today}
        stale = [k for k, v in self._shown.items() if v != today]
        for hid in stale:
            del self._shown[hid]
        if stale and not self._shown:
            self._close_popup_quietly()
        self._prune_snoozes(today)

    def _prune_snoozes(self, today: date) -> None:
        """Drop snoozes of other days (and rewrite kv when something was dropped)."""
        try:
            raw = self._ctx.repo.kv_get(SNOOZES_KEY)
        except sqlite3.Error:
            raw = None
        kept = {k: v for k, v in self._snoozes.items() if v["date"] == today}
        stored_ids = set(raw) if isinstance(raw, dict) else set()
        if len(kept) != len(self._snoozes) or stored_ids != {str(k) for k in kept}:
            self._snoozes = kept
            if raw is not None or kept:
                self._save_snoozes()

    def _track_signatures(self, habits: dict[int, Habit], now: datetime, today: date, dsh: int) -> None:
        """A habit created or re-timed while Madhura runs, after its time today, is not reminded
        today (the user is looking at it right now)."""
        fresh = {hid: (h.time, h.days, h.reminder) for hid, h in habits.items()}
        if self._signatures_ready:
            for hid, sig in fresh.items():
                if self._signatures.get(hid) == sig:
                    continue
                due = hcore.due_at(habits[hid], today, dsh)
                if due is not None and now >= due:
                    self._first_seen[hid] = True
                else:
                    self._first_seen.pop(hid, None)
        self._signatures = fresh
        self._signatures_ready = True

    def _auto_done(self, habits: Any, today: date) -> set[int]:
        autos = [h for h in habits if h.is_auto]
        if not autos:
            return set()
        start, end = self._ctx.day_bounds(today)
        sessions = self._ctx.repo.sessions_between(start, end)
        return {h.id for h in autos if hcore.auto_satisfied(h, hcore.focused_minutes(sessions, h.project))}

    # ================================================================== delivery

    def _holding_all(self) -> bool:
        try:
            return self._ctx.attention.state() in HOLD_ATTENTION
        except Exception:  # noqa: BLE001 - attention probing must never break reminders
            log.debug("attention.state() failed", exc_info=True)
            return False

    def _deliver(self, habits: dict[int, Habit] | None = None, now: datetime | None = None) -> None:
        ctx = self._ctx
        if habits is None:
            habits = {h.id: h for h in ctx.repo.habits()}
        now = now or ctx.clock.now()
        if self._holding_all():
            if self._shown:
                # Presenting / quiet / away: take the panel down without counting a snooze.
                for hid, d in self._shown.items():
                    self._held.setdefault(hid, d)
                self._shown.clear()
                self._close_popup_quietly()
                self.reminders_changed.emit()
            return
        focus = str(ctx.engine.state) == "focus"
        ready = [hid for hid in self._held if hid in habits and (not focus or habits[hid].urgent)]
        for hid in ready:
            self._shown[hid] = self._held.pop(hid)
        if ready:
            self.reminders_changed.emit()
        self._sync_popup(habits, now, play=bool(ready))

    def _items(self, habits: dict[int, Habit], now: datetime) -> list[ReminderItem]:
        from madhura.ui.overlays.habit_reminder_popup import ReminderItem

        ctx = self._ctx
        dsh = ctx.day_start_hour()
        use_24h = ctx.use_24h()
        out: list[ReminderItem] = []
        ordered = sorted((habits[h] for h in self._shown if h in habits), key=lambda h: hcore.sort_key(h, day_start_hour=dsh))
        for h in ordered:
            late = hcore.minutes_late(h, now, day_start_hour=dsh)
            due = hcore.due_at(h, self._shown[h.id], dsh)
            parts = [p for p in ((h.cue or "").strip(), fmt_time(due, use_24h) if due else "") if p]
            out.append(
                ReminderItem(
                    habit_id=h.id,
                    name=h.name,
                    emoji=h.emoji,
                    label=hcore.lateness_label(late),
                    minutes_late=late,
                    detail=" · ".join(parts),
                    day=self._shown[h.id],
                    urgent=bool(h.urgent),
                )
            )
        return out

    def _sync_popup(self, habits: dict[int, Habit], now: datetime, *, play: bool) -> None:
        items = self._items(habits, now)
        popup = self.popup
        if not items:
            if popup is not None and popup.is_open() and not popup.is_confirming():
                self._close_popup_quietly()
            return
        if popup is None:
            popup = self._make_popup()
        popup.set_items(items)
        if not popup.is_open() or popup.is_closing():
            # Re-presenting interrupts a quiet exit animation, whose ``closed`` then never comes.
            self._closing_quietly = False
            popup.present()
        if play:
            try:
                self._ctx.sound.play("habit")
            except Exception:  # noqa: BLE001 - sound must never break reminders
                log.debug("Habit sound failed", exc_info=True)

    def _make_popup(self) -> HabitReminderPopup:
        from madhura.ui.overlays.habit_reminder_popup import HabitReminderPopup

        popup = HabitReminderPopup(self._ctx)
        popup.actioned.connect(self._on_popup_action)
        popup.open_requested.connect(self._on_open_requested)
        popup.closed.connect(self._on_popup_closed)
        self.popup = popup
        return popup

    def _close_popup_quietly(self) -> None:
        popup = self.popup
        if popup is None or not popup.is_open():
            return
        self._closing_quietly = True
        try:
            popup.dismiss()
        finally:
            if not popup.is_open():
                self._closing_quietly = False

    # ================================================================== panel actions

    def _on_popup_action(self, habit_id: int, action: str) -> None:
        ctx = self._ctx
        hid = int(habit_id)
        if ctx.repo.closed:
            return
        habit = ctx.repo.get_habit(hid)
        last = list(self._shown) == [hid] and not self._held_ready()
        if action == "done":
            self.mark(hid, "done")
        elif action == "skip":
            self.mark(hid, "skipped")
        else:
            self.snooze(hid)
        popup = self.popup
        if popup is None:
            return
        if action == "done" and last and habit is not None:
            popup.show_confirmation(self._done_text(habit))
            return
        habits = {h.id: h for h in ctx.repo.habits()}
        self._sync_popup(habits, ctx.clock.now(), play=False)

    def _held_ready(self) -> bool:
        focus = str(self._ctx.engine.state) == "focus"
        return bool(self._held) and not focus and not self._holding_all()

    def _done_text(self, habit: Habit) -> str:
        ctx = self._ctx
        today = ctx.today()
        try:
            created = hcore.created_date(habit) or today
            logs = ctx.repo.habit_logs_between(min(created, today) - timedelta(days=1), today)
            current, _best = hcore.habit_streak(habit, logs, today)
        except sqlite3.Error:
            current = 0
        name = habit.name if len(habit.name) <= 28 else habit.name[:27].rstrip() + "…"
        if current >= 2:
            return f"{name} — done. {current}-day streak."
        return f"{name} — done for today."

    def _on_open_requested(self) -> None:
        window = self._ctx.window
        if window is not None:
            window.show_main("habits", activate=True)

    def _on_popup_closed(self) -> None:
        if self._closing_quietly:
            self._closing_quietly = False
            return
        ctx = self._ctx
        if not self._shown or ctx.repo.closed or ctx.flags.get("quitting") or ctx.flags.get("shut_down"):
            self._shown.clear()
            return
        # Closing the panel = snooze 15 min for everything it listed.
        for hid in list(self._shown):
            self.snooze(hid)

    # ================================================================== signal handlers

    def _on_startup(self) -> None:
        if self._ctx.repo.closed:
            return
        self.auto_complete()
        self.check()

    def _on_engine_state(self, state: str) -> None:
        if str(state) != "focus" and self._held:
            self._deliver()

    def _on_session_finished(self, session_id: int, outcome: str, _restored: bool) -> None:
        ctx = self._ctx
        if str(outcome) not in COUNTED_OUTCOMES or ctx.repo.closed:
            return
        today = ctx.today()
        days = {today}
        try:
            session = ctx.repo.get_session(int(session_id))
        except sqlite3.Error:
            session = None
        if session is not None:
            days.add(logical_date(session.started_at, ctx.day_start_hour()))
        for d in sorted(days):
            self.auto_complete(d)

    def _on_day_changed(self) -> None:
        if self._ctx.repo.closed:
            return
        self._roll_day(self._ctx.today())
        self.auto_complete()
        self.check()

    def _on_data_changed(self, kind: str) -> None:
        if kind in ("habits", "all", "sessions"):
            self._soon.start(0)

    def _on_attention(self, state: str) -> None:
        if self._ctx.repo.closed:
            return
        if str(state) in HOLD_ATTENTION:
            if self._shown:
                self._deliver()
        elif self._held:
            self._deliver()

    # ================================================================== persistence

    def _load_snoozes(self) -> dict[int, dict[str, Any]]:
        try:
            raw = self._ctx.repo.kv_get(SNOOZES_KEY, {}) or {}
        except sqlite3.Error:
            return {}
        out: dict[int, dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            try:
                out[int(key)] = {
                    "until": from_iso(value["until"]),
                    "count": int(value["count"]),
                    "date": from_date_str(value["date"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _save_snoozes(self) -> None:
        payload = {
            str(hid): {"until": to_iso(v["until"]), "count": int(v["count"]), "date": to_date_str(v["date"])}
            for hid, v in self._snoozes.items()
            if v.get("until") is not None and v.get("date") is not None
        }
        try:
            self._ctx.repo.kv_set(SNOOZES_KEY, payload or None)
        except sqlite3.Error:
            log.exception("Could not save habit snoozes")
