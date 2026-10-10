"""FocusPage container [P1]: state routing, session-end ownership, break end, Space/C keys,
after_start "mini", away banner, firm-nudge banner + status line, pending summary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from madhura.context import AppContext, FakeWindowController
from madhura.ui.overlays import session_complete
from madhura.ui.pages.focus.idle_view import NUDGE_TEXT
from madhura.ui.pages.focus_page import AFTER_START_HIDE_MS, FocusPage
from madhura.ui.widgets.sheet import ConfirmSheet
from test_p1_support import TODAY, advance, focus, key, no_summary, page_of, pump, show, tray_focus, win_of  # noqa: F401


# =========================================================================== routing


def test_stack_follows_engine_state(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    assert page.is_idle_shown() and page.stack.currentWidget() is page.idle_view
    focus.engine.start("Write", 25)
    assert page.stack.currentWidget() is page.active_view
    focus.engine.pause()
    assert page.stack.currentWidget() is page.active_view
    focus.engine.resume()
    advance(focus, 120)
    focus.engine.finish()
    assert page.is_idle_shown()
    focus.engine.start_break(5, "Lunch")
    assert page.stack.currentWidget() is page.active_view
    focus.engine.skip_break()
    assert page.is_idle_shown()


def test_focus_never_falls_onto_active_buttons(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    card = page.idle_view.plan_card
    card.task_field.line_edit.setFocus()
    card.task_field.set_text("Write")
    key(card.task_field.line_edit, Qt.Key.Key_Return)
    pump()
    fw = QApplication.focusWidget()
    assert fw is page or fw is None or not isinstance(fw, QLineEdit) or not page.idle_view.isAncestorOf(fw)
    # Space now pauses (focus is on the page, not a button of the active view)
    key(page, Qt.Key.Key_Space, text=" ")
    assert str(focus.engine.state) == "paused"


def test_session_finished_routes_to_summary_exactly_once(focus: AppContext, no_summary: list[Any]) -> None:  # noqa: F811
    sid = focus.engine.start("Write", 25)
    advance(focus, 25 * 60)
    assert str(focus.engine.state) == "idle"
    assert no_summary == [(focus, sid, "completed", False)]
    sid2 = focus.engine.start("Again", 25)
    advance(focus, 300)
    focus.engine.finish()
    assert no_summary[-1] == (focus, sid2, "ended_early", False)
    assert len(no_summary) == 2
    sid3 = focus.engine.start("Too short", 25)
    focus.engine.abandon()
    assert no_summary[-1] == (focus, sid3, "abandoned", False) and len(no_summary) == 3


def test_restored_session_routes_with_restored_flag(focus: AppContext, no_summary: list[Any]) -> None:  # noqa: F811
    focus.engine.session_finished.emit(41, "completed", True)
    assert no_summary == [(focus, 41, "completed", True)]


def test_restore_after_long_gap_shows_away_banner(qapp, data_dir, fake_clock, monkeypatch) -> None:  # noqa: ANN001
    """Madhura closed mid-session for 3 h: restore() pauses + away_detected → the away banner."""
    from test_p1_support import dispose_shell, make_shell

    monkeypatch.setattr(session_complete, "present_session_complete", lambda *a: None)
    monkeypatch.setattr(session_complete, "show_pending_complete", lambda *_a: None)
    ctx = make_shell(qapp, data_dir, fake_clock)
    ctx.engine.start("Long gone", 45)
    advance(ctx, 600)
    dispose_shell(ctx, qapp)  # shutdown checkpoints the running session
    fake_clock.advance(3 * 3600)
    ctx2 = make_shell(qapp, data_dir, fake_clock)  # start_services → engine.restore()
    try:
        show(ctx2)
        page = page_of(ctx2)
        assert str(ctx2.engine.state) == "paused" and not page.is_idle_shown()
        assert page.away_banner_visible() and page.away_banner.text() == "You were away 3h"
        assert page.away_banner.action_buttons()[1].text() == "Finish (save 10 min)"
    finally:
        dispose_shell(ctx2, qapp)


def test_summary_errors_do_not_break_the_chain(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    def boom(*_a: Any) -> None:
        raise RuntimeError("P2 bug")

    monkeypatch.setattr(session_complete, "present_session_complete", boom)
    focus.engine.start("Write", 25)
    advance(focus, 120)
    focus.engine.finish()
    assert page_of(focus).is_idle_shown()


# =========================================================================== break end


def test_break_finished_natural_active_window(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    played: list[str] = []
    notes: list[tuple[str, str]] = []
    monkeypatch.setattr(focus.sound, "play", lambda ev: played.append(ev) or True)
    monkeypatch.setattr(focus.notifier, "notify", lambda t, b, *a: notes.append((t, b)) or True)
    win = win_of(focus)
    focus_calls: list[int] = []
    monkeypatch.setattr(win, "is_main_active", lambda: True)
    monkeypatch.setattr(win, "focus_task_input", lambda: focus_calls.append(1))
    focus.repo.add_task("Next up", 25, plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    focus.engine.start_break(1)
    advance(focus, 61, step=30)
    pump()
    assert played == ["break_over"]
    assert notes == []  # window visible → no notification
    assert focus_calls == [1]
    page = page_of(focus)
    assert page.is_idle_shown() and page.idle_view.plan_card.task_field.text() == "Next up"


def test_break_finished_natural_hidden_window_notifies(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    played: list[str] = []
    notes: list[tuple[str, str]] = []
    monkeypatch.setattr(focus.sound, "play", lambda ev: played.append(ev) or True)
    monkeypatch.setattr(focus.notifier, "notify", lambda t, b, *a: notes.append((t, b)) or True)
    win = win_of(focus)
    focus_calls: list[int] = []
    monkeypatch.setattr(win, "focus_task_input", lambda: focus_calls.append(1))
    focus.engine.start_break(1)
    win.hide()
    advance(focus, 61, step=30)
    pump()
    assert played == ["break_over"]
    assert notes == [("Break's over", "Plan your next block.")]
    assert focus_calls == []  # never steal focus


def test_break_skipped_is_silent(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    played: list[str] = []
    monkeypatch.setattr(focus.sound, "play", lambda ev: played.append(ev) or True)
    focus.engine.start_break(5)
    focus.engine.skip_break()
    assert played == [] and page_of(focus).is_idle_shown()


# =========================================================================== keyboard


def test_space_toggles_pause_only_when_appropriate(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    page.setFocus()
    key(page, Qt.Key.Key_Space, text=" ")  # idle → nothing
    assert str(focus.engine.state) == "idle"
    focus.engine.start("Write", 25)
    page.setFocus()
    key(page, Qt.Key.Key_Space, text=" ")
    assert str(focus.engine.state) == "paused"
    key(page, Qt.Key.Key_Space, text=" ")
    assert str(focus.engine.state) == "focus"
    # A text input with focus keeps Space for itself.
    edit = QLineEdit(page)
    edit.show()
    edit.setFocus()
    pump()
    assert QApplication.focusWidget() is edit
    key(page, Qt.Key.Key_Space, text=" ")
    assert str(focus.engine.state) == "focus"
    # So does a button.
    btn = page.idle_view.plan_card.lock_in
    page.idle_view.show()
    btn.setFocus()
    if QApplication.focusWidget() is btn:
        key(page, Qt.Key.Key_Space, text=" ")
        assert str(focus.engine.state) == "focus"
    edit.deleteLater()


def test_space_ignored_during_break(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    focus.engine.start_break(5)
    page.setFocus()
    key(page, Qt.Key.Key_Space, text=" ")
    assert str(focus.engine.state) == "break"


def test_c_focuses_capture(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    page = page_of(focus)
    calls: list[int] = []
    monkeypatch.setattr(page.active_view, "focus_capture", lambda: calls.append(1))
    page.setFocus()
    key(page, Qt.Key.Key_C, text="c")  # idle → nothing
    assert calls == []
    focus.engine.start("Write", 25)
    page.setFocus()
    key(page, Qt.Key.Key_C, text="c")
    assert calls == [1]


def test_start_from_input_and_focus_task_input_by_state(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    page = page_of(focus)
    calls: list[str] = []
    monkeypatch.setattr(page.active_view, "start_from_input", lambda: calls.append("start") or 7)
    monkeypatch.setattr(page.active_view, "focus_task_input", lambda: calls.append("focus"))
    focus.engine.start_break(5)
    assert page.start_from_input() == 7
    page.focus_task_input()
    assert calls == ["start", "focus"]
    focus.engine.skip_break()
    focus.engine.start("Write", 25)
    assert page.start_from_input() is None  # running: nothing to start
    page.focus_task_input()
    assert calls == ["start", "focus"]


# =========================================================================== after_start "mini"


def test_after_start_mini_hides_window(tray_focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    ctx = tray_focus
    assert ctx.settings.get("after_start") == "mini"
    win = win_of(ctx)
    card = page_of(ctx).idle_view.plan_card
    card.task_field.set_text("Deep work")
    card.start()
    assert win.isVisible()
    qtbot.wait(AFTER_START_HIDE_MS - 400)
    assert win.isVisible()  # still sweeping the arc
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=2000)
    assert str(ctx.engine.state) == "focus"


def test_after_start_stay_keeps_window(tray_focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    ctx = tray_focus
    ctx.settings.update(after_start="stay")
    win = win_of(ctx)
    ctx.engine.start("Deep work", 25)
    qtbot.wait(AFTER_START_HIDE_MS + 200)
    assert win.isVisible()


def test_after_start_mini_skipped_when_paused_or_hidden(tray_focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    ctx = tray_focus
    win = win_of(ctx)
    ctx.engine.start("Deep work", 25)
    ctx.engine.pause()  # user paused within the 900 ms
    qtbot.wait(AFTER_START_HIDE_MS + 200)
    assert win.isVisible()
    ctx.engine.abandon()
    win.hide()
    hides = []
    win.hide_main = lambda: hides.append(1)  # type: ignore[method-assign]
    ctx.engine.start("Started from the pill", 25)
    qtbot.wait(AFTER_START_HIDE_MS + 200)
    assert hides == []


# =========================================================================== away banner


def _go_away(ctx: AppContext) -> None:
    ctx.engine.start("Prepare slides", 45)
    advance(ctx, 600)
    ctx.clock.advance(2 * 3600)  # laptop asleep
    ctx.engine.poll()


def test_away_banner_resume(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    _go_away(focus)
    assert str(focus.engine.state) == "paused"
    assert page.away_banner_visible()
    assert page.away_banner.text() == "You were away 2h"
    labels = [b.text() for b in page.away_banner.action_buttons()]
    assert labels == ["Resume", "Finish (save 10 min)", "Abandon"]
    page.away_banner.action_buttons()[0].click()
    assert str(focus.engine.state) == "focus" and not page.away_banner_visible()


def test_away_banner_finish_saves(focus: AppContext, no_summary: list[Any]) -> None:  # noqa: F811
    page = page_of(focus)
    _go_away(focus)
    page.away_banner.action_buttons()[1].click()
    assert page.is_idle_shown() and not page.away_banner_visible()
    sid, outcome = no_summary[-1][1], no_summary[-1][2]
    assert outcome == "ended_early" and focus.repo.get_session(sid).focused_seconds == 600


def test_away_banner_abandon_confirms(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    _go_away(focus)
    page.away_banner.action_buttons()[2].click()
    sheet = win_of(focus).sheet_host.current()
    assert isinstance(sheet, ConfirmSheet)
    sheet.reject()
    pump()
    assert str(focus.engine.state) == "paused"
    page.away_banner.action_buttons()[2].click()
    sheet = win_of(focus).sheet_host.current()
    sheet.accept()
    pump()
    assert str(focus.engine.state) == "idle"


def test_away_banner_dismiss(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    _go_away(focus)
    page.away_banner.dismiss()
    assert not page.away_banner_visible() and str(focus.engine.state) == "paused"


# =========================================================================== firm nudge banner + status line


def test_firm_nudge_banner_visibility_idle(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    banner = page.idle_view.nudge_banner
    assert not banner.isVisible()
    focus.window.set_nudge_banner(True)  # the WindowController path NudgeService uses
    assert banner.isVisible() and banner.text() == NUDGE_TEXT
    labels = [b.text() for b in banner.action_buttons()]
    assert len(labels) == 3 and labels[1:] == ["Snooze ▾", "Done for today"]
    assert labels[0]  # one-click primary from ctx.nudges.primary_action()
    page.set_nudge_banner(False)
    assert not banner.isVisible()


def test_firm_nudge_done_for_today_and_status_line(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    idle = page.idle_view
    page.set_nudge_banner(True)
    idle.nudge_banner.action_buttons()[2].click()
    assert not idle.nudge_banner.isVisible()
    assert focus.nudges.is_done_for_today
    pump()
    status = idle.nudge_status
    assert status.isVisible()
    assert status.text_label.text() == "Done for today ·" and status.resume.text() == "Resume nudges"
    status.resume.click()
    assert not focus.nudges.is_done_for_today and not status.isVisible()


def test_snooze_status_line_and_menu(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    focus.nudges.snooze(30)
    pump()
    status = idle.nudge_status
    assert status.isVisible() and status.text_label.text() == "Nudges snoozed until 10:30 ·"
    assert status.resume.text() == "Resume"
    status.resume.click()
    assert not status.isVisible()
    menu = idle.snooze_menu()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["15 min", "30 min", "1 hour", "In a meeting…"]
    idle.set_nudge_banner(True)
    next(a for a in menu.actions() if a.text() == "1 hour").trigger()
    assert not idle.nudge_banner.isVisible()
    assert focus.nudges.snoozed_until == datetime(2026, 10, 14, 11, 0)


def test_snooze_in_a_meeting_starts_meeting_block(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    menu = idle.snooze_menu()
    meeting = next(a for a in menu.actions() if a.text() == "In a meeting…").menu()
    next(a for a in meeting.actions() if a.text() == "1 hour").trigger()
    e = focus.engine
    assert str(e.state) == "break" and e.break_label == "Meeting" and e.break_total_seconds == 3600


def test_status_line_hidden_when_nudges_off(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    focus.nudges.snooze(30)
    focus.settings.update(nudge_mode="off")
    pump()
    assert not idle.nudge_status.isVisible()


def test_paused_nudge_banner_copy_and_cleared_on_resume(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    focus.engine.start("Prepare slides", 25)
    focus.engine.pause()
    page.set_nudge_banner(True)
    assert page.paused_nudge_banner.isVisibleTo(page) and page.active_banners.isVisibleTo(page)
    assert page.paused_nudge_banner.text() == "Still paused on ‘Prepare slides’"
    assert [b.text() for b in page.paused_nudge_banner.action_buttons()] == ["Resume", "Finish", "Snooze ▾"]
    assert not page.idle_view.nudge_banner.isVisibleTo(page.idle_view)
    page.paused_nudge_banner.action_buttons()[0].click()
    assert str(focus.engine.state) == "focus"
    assert not page.nudge_banner_visible() and not page.active_banners.isVisibleTo(page)


def test_stale_nudge_banner_dropped_when_session_starts(focus: AppContext) -> None:  # noqa: F811
    page = page_of(focus)
    page.set_nudge_banner(True)
    focus.engine.start("Write", 25)
    assert not page.nudge_banner_visible()
    focus.engine.abandon()
    assert not page.idle_view.nudge_banner.isVisible()


def test_nudge_primary_action_fallback_without_service(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    focus.nudges = None
    idle.set_nudge_banner(True)
    first = idle.nudge_banner.action_buttons()[0]
    assert first.text() == "Plan now"
    first.click()
    pump()
    assert idle.plan_card.task_field.line_edit.hasFocus() and not idle.nudge_banner.isVisible()


# =========================================================================== pending summary + misc


def test_main_visibility_shows_pending_summary(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    calls: list[Any] = []
    monkeypatch.setattr(session_complete, "show_pending_complete", lambda ctx: calls.append(ctx))
    focus.events.main_visibility_changed.emit(False)
    assert calls == []
    focus.events.main_visibility_changed.emit(True)
    assert calls == [focus]
    win = win_of(focus)
    win.hide()
    show(focus)
    assert calls[-1] is focus and len(calls) == 2


def test_on_shown_hooks(focus: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    page = page_of(focus)
    calls: list[str] = []
    monkeypatch.setattr(page.active_view, "on_shown", lambda: calls.append("active"))
    page.on_shown()
    assert calls == []
    focus.engine.start("Write", 25)
    page.on_shown()
    assert calls == ["active"]


def test_focus_page_standalone_with_fake_window(qapp, data_dir, fake_clock, no_summary) -> None:  # noqa: ANN001, F811
    """FocusPage works against the WindowController protocol (FakeWindowController)."""
    from madhura.app import create_context, start_services

    ctx = create_context(qapp, data_dir, fake_clock, tray_available=False)
    ctx.settings.update(reduce_motion=True, onboarded=True, after_start="mini")
    fake = FakeWindowController()
    ctx.window = fake  # type: ignore[assignment]
    page = FocusPage(ctx)
    start_services(ctx)
    try:
        page.set_nudge_banner(True)
        assert page.nudge_banner_visible()
        tid = ctx.repo.add_task("Delete me", plan_date=TODAY)
        ctx.events.data_changed.emit("tasks")
        page.idle_view.refresh()
        page.idle_view.up_next.delete_task(tid)
        assert fake.toasts[-1][2] == "Undo"
        fake.toast_actions[-1]()  # Undo
        assert [t.id for t in page.idle_view.up_next.tasks()] == [tid]
        page.idle_view.up_next.delete_task(tid)
        page.idle_view.flush_pending()
        assert ctx.repo.get_task(tid) is None
        page.idle_view.open_wrap_up()
        assert fake.called("show_sheet") == 1
    finally:
        from madhura.app import shutdown

        page.deleteLater()
        shutdown(ctx)
