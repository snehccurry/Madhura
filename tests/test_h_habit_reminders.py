"""HabitReminderService + HabitReminderPopup [H] (spec §1.8 reminders and auto-complete)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication
from test_h_support import (
    TODAY,
    add_habit,
    add_session,
    advance,
    habits_page,
    hshell_context,
    log,
    pump,
    set_now,
)

from madhura.context import AppContext
from madhura.core.clock import FakeClock
from madhura.core.models import to_iso
from madhura.services.habit_reminders import SNOOZES_KEY, HabitReminderService
from madhura.ui.overlays.habit_reminder_popup import (
    MAX_ROWS,
    HabitReminderPopup,
    ReminderItem,
    caption_for,
)
from madhura.ui.widgets.popup import FloatingPanel, PopupStack


@pytest.fixture
def hshell(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> Iterator[AppContext]:
    """Running shell for habits tests (see ``test_h_support.hshell_context``)."""
    yield from hshell_context(qapp, data_dir, fake_clock)


def _svc(ctx: AppContext) -> HabitReminderService:
    svc = ctx.habit_reminders
    assert isinstance(svc, HabitReminderService)
    return svc


def _sounds(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    played: list[str] = []
    monkeypatch.setattr(ctx.sound, "play", lambda event: played.append(event) or True)
    return played


def _status(ctx: AppContext, hid: int, d: date = TODAY) -> str | None:
    for lg in ctx.repo.habit_logs_for_date(d):
        if lg.habit_id == hid:
            return str(lg.status)
    return None


def _closed(qtbot: Any, popup: HabitReminderPopup | None) -> None:
    """Wait for the (opacity-only, 100 ms) exit animation."""
    assert popup is not None
    qtbot.waitUntil(lambda: not popup.is_open(), timeout=2000)


def _popup_ids(svc: HabitReminderService) -> list[int]:
    popup = svc.popup
    if popup is None or not popup.is_open():
        return []
    return [i.habit_id for i in popup.items()]


# =========================================================================== due logic


def test_due_habit_shows_popup_with_sound(hshell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = hshell
    played = _sounds(ctx, monkeypatch)
    svc = _svc(ctx)
    hid = add_habit(ctx, "Meditate", time="09:50", emoji="🧘", cue="After coffee")
    assert svc.check() == [hid]
    popup = svc.popup
    assert isinstance(popup, HabitReminderPopup) and isinstance(popup, FloatingPanel)
    assert popup.is_open() and PopupStack.instance().contains(popup)
    assert popup.priority == 3 and popup.kind == "habit"
    assert popup.caption_text() == "Daily habit"
    (item,) = popup.items()
    assert (item.name, item.emoji, item.label, item.detail, item.day) == ("Meditate", "🧘", "10 min late", "After coffee · 09:50", TODAY)
    assert played == ["habit"]
    # re-checking does not duplicate or replay the sound
    assert svc.check() == []
    assert _popup_ids(svc) == [hid] and played == ["habit"]


def test_not_due_cases(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    later = add_habit(ctx, "Later", time="10:30")
    off = add_habit(ctx, "Quiet", time="09:00", reminder=False)
    anytime = add_habit(ctx, "Anytime")
    weekend = add_habit(ctx, "Weekend", time="09:00", days=0b1100000)
    done = add_habit(ctx, "Done", time="09:00")
    log(ctx, done, TODAY, "done")
    skipped = add_habit(ctx, "Skipped", time="09:00")
    log(ctx, skipped, TODAY, "skipped")
    new = ctx.repo.add_habit("Created after its time", time="09:00")  # created_at = 10:00
    assert svc.check() == []
    assert svc.popup is None and svc.queued_count == 0
    assert all(not svc.is_suppressed(h) for h in (later, off, anytime, weekend, done, skipped, new))


def test_lateness_cap_suppresses_for_the_day(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    capped = add_habit(ctx, "Early", time="07:59")  # 121 min late at 10:00
    shown = add_habit(ctx, "Recent", time="08:01")  # 119 min late
    log(ctx, capped, TODAY - timedelta(days=1), "done")
    svc.check()
    assert _popup_ids(svc) == [shown]
    assert svc.is_suppressed(capped) and not svc.is_suppressed(shown)
    set_now(ctx, datetime(2026, 10, 14, 11, 0))
    svc.check()
    assert capped not in _popup_ids(svc)
    # the page shows it as pending
    page = habits_page(ctx)
    assert page.model(capped).pending and page.model(capped).caption.endswith("Pending")


def test_lateness_cap_resets_next_day(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Early", time="07:00")
    svc.check()
    assert svc.is_suppressed(hid)
    set_now(ctx, datetime(2026, 10, 15, 7, 5))
    ctx.events.day_changed.emit()
    assert not svc.is_suppressed(hid)
    assert _popup_ids(svc) == [hid] and svc.popup.items()[0].day == date(2026, 10, 15)


def test_edited_after_its_time_is_not_reminded_today(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Water", time="20:00")
    svc.check()
    ctx.repo.update_habit(hid, time="09:30")  # moved to a time that already passed
    ctx.events.data_changed.emit("habits")
    svc.check()
    assert svc.popup is None and svc.is_suppressed(hid)
    ctx.repo.update_habit(hid, time="10:20")  # moved to the future → reminded normally
    svc.check()
    assert not svc.is_suppressed(hid)
    set_now(ctx, datetime(2026, 10, 14, 10, 21))
    svc.check()
    assert _popup_ids(svc) == [hid]


# =========================================================================== popup actions


def test_done_action_logs_confirms_and_closes(hshell: AppContext, qtbot: Any) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Meditate", time="09:45")
    for i in range(1, 4):
        log(ctx, hid, TODAY - timedelta(days=i), "done")
    seen: list[str] = []
    ctx.events.data_changed.connect(seen.append)
    svc.check()
    popup = svc.popup
    popup.rows()[0].done_button.click()
    assert _status(ctx, hid) == "done" and "habits" in seen
    assert popup.is_confirming() and "4-day streak" in popup.confirm_box.text.text()
    assert svc.shown_ids() == []
    popup._finish_confirmation()  # noqa: SLF001 - the 1.2 s timer
    _closed(qtbot, popup)
    assert svc.snooze_info(hid) is None  # closing after Done is not a snooze


def test_skip_action(hshell: AppContext, qtbot: Any) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Run", time="09:45")
    svc.check()
    svc.popup.rows()[0].skip_button.click()
    assert _status(ctx, hid) == "skipped"
    _closed(qtbot, svc.popup)


def test_snooze_persists_and_cap_after_three(hshell: AppContext, qtbot: Any) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Stretch", time="09:55")
    svc.check()
    svc.popup.rows()[0].snooze_button.click()
    pump()
    info = svc.snooze_info(hid)
    assert info is not None and info["count"] == 1 and info["until"] == datetime(2026, 10, 14, 10, 15)
    assert ctx.repo.kv_get(SNOOZES_KEY) == {str(hid): {"until": "2026-10-14T10:15:00", "count": 1, "date": "2026-10-14"}}
    _closed(qtbot, svc.popup)
    set_now(ctx, datetime(2026, 10, 14, 10, 14))
    assert svc.check() == [] and _popup_ids(svc) == []
    for n in (2, 3):
        set_now(ctx, ctx.clock.now() + timedelta(minutes=16))
        svc.check()
        assert _popup_ids(svc) == [hid], n
        svc.popup.rows()[0].snooze_button.click()
        _closed(qtbot, svc.popup)
        assert svc.snooze_info(hid)["count"] == n
    set_now(ctx, ctx.clock.now() + timedelta(minutes=30))
    assert svc.check() == [] and _popup_ids(svc) == []  # 3 snoozes → done reminding today


def test_close_button_snoozes_every_listed_habit(hshell: AppContext, qtbot: Any) -> None:
    ctx = hshell
    svc = _svc(ctx)
    a = add_habit(ctx, "A", time="09:40")
    b = add_habit(ctx, "B", time="09:50")
    svc.check()
    popup = svc.popup
    assert popup.caption_text() == "2 habits due" and len(popup.rows()) == 2 and popup.rows()[0].compact
    popup.close_button.click()
    _closed(qtbot, popup)
    assert svc.snooze_info(a)["count"] == 1 and svc.snooze_info(b)["count"] == 1


def test_snoozes_survive_a_restart(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Stretch", time="09:55")
    svc.check()
    svc.snooze(hid)
    svc.snooze(hid)
    fresh = HabitReminderService(ctx)
    fresh._startup.stop()  # noqa: SLF001
    try:
        info = fresh.snooze_info(hid)
        assert info is not None and info["count"] == 2 and info["until"] == datetime(2026, 10, 14, 10, 15)
        assert fresh.check() == []
    finally:
        fresh.stop()


def test_stale_and_invalid_snoozes_are_ignored(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Stretch", time="09:55")
    ctx.repo.kv_set(SNOOZES_KEY, {str(hid): {"until": "2026-10-13T23:00:00", "count": 3, "date": "2026-10-13"}, "x": {"bad": 1}})
    fresh = HabitReminderService(ctx)
    fresh._startup.stop()  # noqa: SLF001
    try:
        assert fresh.snooze_info(hid) is None
        assert fresh.check() == [hid]
        assert str(hid) not in (ctx.repo.kv_get(SNOOZES_KEY) or {})
    finally:
        if fresh.popup is not None:
            fresh.popup.hide()
        fresh.stop()


def test_marking_done_elsewhere_removes_the_row(hshell: AppContext, qtbot: Any) -> None:
    ctx = hshell
    svc = _svc(ctx)
    a = add_habit(ctx, "A", time="09:40")
    b = add_habit(ctx, "B", time="09:50")
    svc.check()
    page = habits_page(ctx)
    page.toggle_done(a)
    svc.check()
    assert _popup_ids(svc) == [b] and svc.popup.caption_text() == "Daily habit"
    page.toggle_done(b)
    svc.check()
    _closed(qtbot, svc.popup)
    assert svc.snooze_info(a) is None and svc.snooze_info(b) is None


def test_more_than_three_due(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    ids = [add_habit(ctx, f"H{i}", time=f"09:{40 + i}") for i in range(5)]
    svc.check()
    popup = svc.popup
    assert len(popup.rows()) == MAX_ROWS and popup.total() == 5
    assert popup.caption_text() == "5 habits due"
    assert popup.more_row.isVisibleTo(popup) and popup.more_label.text() == "+2 more ·"
    popup.rows()[0].done_button.click()
    pump()
    assert [i.habit_id for i in popup.items()] == ids[1:4] and popup.total() == 4
    opened: list[Any] = []
    ctx.window.show_main = lambda page=None, activate=True: opened.append((page, activate))  # type: ignore[method-assign, union-attr]
    popup.open_link.click()
    assert opened == [("habits", True)]


def test_popup_buttons_never_take_focus(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    add_habit(ctx, "A", time="09:40")
    svc.check()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractButton

    popup = svc.popup
    assert popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert all(b.focusPolicy() == Qt.FocusPolicy.NoFocus for b in popup.findChildren(QAbstractButton))


# =========================================================================== focus / attention holding


def test_focus_holds_non_urgent_and_delivers_batched_when_session_ends(hshell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = hshell
    played = _sounds(ctx, monkeypatch)
    svc = _svc(ctx)
    a = add_habit(ctx, "A", time="10:01")
    b = add_habit(ctx, "B", time="10:02")
    urgent = add_habit(ctx, "Meds", time="10:03", urgent=True)
    ctx.engine.start("Write", 25)
    advance(ctx, 4 * 60)
    svc.check()
    assert svc.queued_count == 2 and svc.held_ids() == [a, b]
    assert _popup_ids(svc) == [urgent] and played == ["habit"]
    svc.popup.rows()[0].done_button.click()
    svc.popup._finish_confirmation()  # noqa: SLF001
    pump()
    ctx.engine.finish()
    pump()
    assert svc.queued_count == 0
    assert _popup_ids(svc) == [a, b] and svc.popup.caption_text() == "2 habits due"
    assert [p for p in played if p == "habit"] == ["habit", "habit"]
    assert svc.popup.items()[0].label == "3 min late"


def test_break_start_delivers_held_reminders(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    ctx.settings.update(auto_start_break=True)
    hid = add_habit(ctx, "A", time="10:05")
    ctx.engine.start("Write", 10)
    advance(ctx, 6 * 60)
    svc.check()
    assert svc.held_ids() == [hid]
    advance(ctx, 4 * 60 + 5)  # natural completion → auto break
    pump()
    assert str(ctx.engine.state) == "break"
    assert _popup_ids(svc) == [hid] and svc.queued_count == 0


def test_attention_quiet_holds_everything_even_urgent(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Meds", time="09:58", urgent=True)
    ctx.attention.set_quiet(30)
    assert ctx.attention.state() == "quiet"
    svc.check()
    assert svc.popup is None and svc.held_ids() == [hid]
    ctx.attention.clear_quiet()
    pump()
    assert _popup_ids(svc) == [hid] and svc.queued_count == 0


def test_busy_attention_takes_an_open_panel_down_without_snoozing(hshell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot: Any) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Meds", time="09:58")
    svc.check()
    popup = svc.popup
    assert popup.is_open()
    monkeypatch.setattr(ctx.attention, "state", lambda: "busy")
    ctx.attention.changed.emit("busy")
    _closed(qtbot, popup)
    assert svc.held_ids() == [hid] and svc.snooze_info(hid) is None
    monkeypatch.setattr(ctx.attention, "state", lambda: "available")
    ctx.attention.changed.emit("available")
    pump()
    assert _popup_ids(svc) == [hid]


# =========================================================================== auto-complete


def test_auto_complete_on_session_finished(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    deep = add_habit(ctx, "Deep work", focus_minutes=20)
    thesis = add_habit(ctx, "Thesis", focus_minutes=20, project="Thesis")
    weekend = add_habit(ctx, "Weekend", focus_minutes=5, days=0b1100000)
    seen: list[str] = []
    ctx.events.data_changed.connect(seen.append)
    ctx.engine.start("Write", 25, project="Other")
    advance(ctx, 25 * 60 + 1)
    pump()
    assert _status(ctx, deep) == "done"
    assert _status(ctx, thesis) is None and _status(ctx, weekend) is None
    assert "habits" in seen
    ctx.engine.start("Chapter 2", 30, project="thesis")
    advance(ctx, 21 * 60)
    ctx.engine.finish()  # ended early still counts
    pump()
    assert _status(ctx, thesis) == "done"
    assert svc.auto_complete() == []  # idempotent


def test_auto_complete_ignores_abandoned_and_respects_existing_logs(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    deep = add_habit(ctx, "Deep work", focus_minutes=20)
    skipped = add_habit(ctx, "Skipped deep work", focus_minutes=20)
    log(ctx, skipped, TODAY, "skipped")
    add_session(ctx, datetime(2026, 10, 14, 8, 0), 60, outcome="abandoned")
    assert svc.auto_complete() == []
    add_session(ctx, datetime(2026, 10, 14, 9, 0), 25)
    assert svc.auto_complete() == [deep]
    assert _status(ctx, skipped) == "skipped"


def test_auto_complete_uses_the_sessions_logical_day(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    deep = add_habit(ctx, "Deep work", focus_minutes=20)
    # a 25-min session that started 03:30 belongs to the previous logical day (day start 04:00)
    sid = add_session(ctx, datetime(2026, 10, 14, 3, 30), 25)
    svc._on_session_finished(sid, "completed", False)  # noqa: SLF001
    assert _status(ctx, deep, date(2026, 10, 13)) == "done"
    assert _status(ctx, deep, TODAY) is None


def test_auto_complete_on_day_changed_and_startup(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    deep = add_habit(ctx, "Deep work", focus_minutes=30)
    add_session(ctx, datetime(2026, 10, 15, 6, 0), 40)
    set_now(ctx, datetime(2026, 10, 15, 8, 0))
    ctx.events.day_changed.emit()
    assert _status(ctx, deep, date(2026, 10, 15)) == "done"
    other = add_habit(ctx, "More deep work", focus_minutes=35)
    svc._on_startup()  # noqa: SLF001 - the delayed startup pass
    assert _status(ctx, other, date(2026, 10, 15)) == "done"


def test_satisfied_auto_habit_is_never_reminded(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Deep work", time="09:30", focus_minutes=20)
    add_session(ctx, datetime(2026, 10, 14, 8, 0), 25)
    assert svc.check() == []
    assert svc.popup is None
    assert hid


# =========================================================================== reset / misc


def test_reset_forgets_everything(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    a = add_habit(ctx, "A", time="09:40")
    b = add_habit(ctx, "B", time="09:50")
    svc.snooze(a)
    ctx.engine.start("Write", 25)
    svc.check()
    assert svc.queued_count == 1
    svc.reset()
    assert svc.queued_count == 0 and svc.snooze_info(a) is None and ctx.repo.kv_get(SNOOZES_KEY) is None
    assert b


def test_day_rollover_drops_yesterdays_state(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Late", time="23:00")
    set_now(ctx, datetime(2026, 10, 14, 23, 5))
    svc.check()
    svc.snooze(hid)
    set_now(ctx, datetime(2026, 10, 15, 9, 0))
    svc.check()
    assert svc.snooze_info(hid) is None and ctx.repo.kv_get(SNOOZES_KEY) is None


def test_popup_standalone_api(qapp: QApplication) -> None:
    PopupStack.reset()
    popup = HabitReminderPopup()
    try:
        items = [ReminderItem(i, f"Habit {i}", None, "Due now" if i == 1 else "45 min late", 0 if i == 1 else 45) for i in (1, 2)]
        popup.set_items(items)
        assert popup.caption_text() == "2 habits due" and caption_for(1) == "Daily habit"
        assert items[0].tone == "muted" and items[1].tone == "warning"
        row = popup.rows()[1]
        assert row.status_label.property("tone") == "warning"
        got: list[tuple[int, str]] = []
        popup.actioned.connect(lambda hid, action: got.append((hid, action)))
        row.snooze_button.click()
        row.skip_button.click()
        row.done_button.click()
        assert got == [(2, "snooze"), (2, "skip"), (2, "done")]
        popup.set_items(items[:1])
        assert len(popup.rows()) == 1 and not popup.rows()[0].compact
        popup.show_confirmation("Habit 1 — done for today.", 0)
        assert popup.is_confirming()
    finally:
        popup.hide()
        PopupStack.reset()


def test_snooze_key_is_iso_and_today(hshell: AppContext) -> None:
    ctx = hshell
    svc = _svc(ctx)
    hid = add_habit(ctx, "Stretch", time="09:55")
    svc.snooze(hid, 30)
    stored = ctx.repo.kv_get(SNOOZES_KEY)[str(hid)]
    assert stored == {"until": to_iso(datetime(2026, 10, 14, 10, 30)), "count": 1, "date": "2026-10-14"}
    assert isinstance(ctx.clock, FakeClock)
