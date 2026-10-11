"""[P2] Focus page active / paused / break view (spec §1.3, §1.5 inline prompt, §1.13, §8)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_shell_support import advance, dispose_shell, make_shell, shell  # noqa: F401

from madhura.context import AppContext
from madhura.core.clock import FakeClock
from madhura.ui import theme
from madhura.ui.pages.focus import active_view as av
from madhura.ui.pages.focus.active_view import ActiveView, CheckInStrip, ClampedText
from madhura.ui.widgets.sheet import ConfirmSheet

# --------------------------------------------------------------------------- helpers


class FakeCheckins(QObject):
    """Stand-in for the [O] CheckInService (same public API as spec §5.3)."""

    prompt_inline = Signal()
    prompt_closed = Signal()
    answered = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.answers: list[tuple[str, str | None]] = []
        self.progress: float | None = 0.8
        self.next_in: float | None = 420.0
        self._counts = {"focused": 0, "drifted": 0, "missed": 0}

    def answer(self, response: str, reason: str | None = None) -> None:
        self.answers.append((response, reason))
        self._counts[response] += 1
        self.answered.emit(response)
        self.prompt_closed.emit()  # a real service closes the prompt after an answer

    def counts(self) -> dict:
        return dict(self._counts)

    def seconds_until_next(self) -> float | None:
        return self.next_in

    def timeout_progress(self) -> float | None:
        return self.progress


def pump(n: int = 5) -> None:
    for _ in range(n):
        QApplication.processEvents()


@pytest.fixture
def view(shell: AppContext, qtbot: Any) -> Iterator[ActiveView]:  # noqa: F811
    """A standalone, shown ActiveView on the shell context with a fake check-in service."""
    shell.checkins = FakeCheckins()  # type: ignore[assignment]
    v = ActiveView(shell)
    v.resize(900, 760)
    v.show()
    qtbot.waitExposed(v)
    v.activateWindow()
    pump()
    yield v
    v.hide()
    v.deleteLater()


def fake(ctx: AppContext) -> FakeCheckins:
    assert isinstance(ctx.checkins, FakeCheckins)
    return ctx.checkins


# --------------------------------------------------------------------------- pure helpers


def test_ring_font_sizes() -> None:
    assert av.ring_font_px("44:59") == 60
    assert av.ring_font_px("1:29:59") == 48


def test_break_copy_and_skip_labels() -> None:
    assert av.break_title("Break") == "Stand up, stretch, look far away."
    assert av.break_title(None) == "Stand up, stretch, look far away."
    for label in ("Lunch", "Meeting", "Admin"):
        assert av.break_title(label) == av.BREAK_COPY[label]
    assert av.break_title("Walk") == av.DEFAULT_BREAK_COPY
    assert av.skip_label("Break") == "Skip break"
    assert av.skip_label("Lunch") == "End lunch"
    assert av.skip_label("Meeting") == "End meeting"
    assert av.skip_label("Walk with Sam") == "End Walk with Sam"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"enabled": False}, "Check-ins off"),
        ({}, "Check-ins: first in ~7 min"),
        ({"counts": {"focused": 3, "drifted": 1}, "next_in": 210.0}, "Check-ins: 3 focused · 1 came back · next in ~4 min"),
        ({"counts": {"focused": 2}, "next_in": 60.0}, "Check-ins: 2 focused · next in ~1 min"),
        ({"counts": {"focused": 3, "missed": 2}, "next_in": None}, "Check-ins: 3 focused"),
        ({"paused": True}, "Check-ins paused"),
        ({"paused": True, "counts": {"focused": 3}}, "Check-ins: 3 focused · paused"),
        ({"next_in": 420.0, "remaining": 300.0}, "No check-ins this block"),
        ({"counts": {"drifted": 1}, "next_in": 420.0, "remaining": 400.0}, "Check-ins: 1 came back"),
        ({"next_in": None}, "Check-ins on"),
    ],
)
def test_checkin_status_text(kwargs: dict, expected: str) -> None:
    args = {"enabled": True, "paused": False, "counts": {}, "next_in": 420.0, "remaining": 2700.0}
    args.update(kwargs)
    assert av.checkin_status_text(**args) == expected


# --------------------------------------------------------------------------- ring + labels


def test_ring_text_and_caption_while_focusing(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    eng.start("Write the report", 45, "Work")
    pump()
    assert view.mode() == "focus"
    assert view.ring.text() == "45:00"
    assert view.ring.caption() == "of 45 min"
    advance(shell, 13, step=1)
    assert view.ring.text() == "44:47"
    assert view.title.text() == "Write the report"
    assert view.meta_label.text() == "Ends at 10:45"
    assert view.project_badge.isVisibleTo(view) and view.project_badge.text() == "Work"
    assert abs(view.ring.progress() - eng.remaining_seconds / eng.planned_seconds) < 0.01
    assert view.ring.accessibleName() == "Time remaining"
    assert view.ring.accessibleDescription() == "45 minutes left of 45"


def test_ring_uses_hours_format_and_smaller_digits_from_one_hour(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    eng.start("Deep work", 61)
    pump()
    assert view.ring.text() == "1:01:00"
    assert view._ring_px == 48  # noqa: SLF001
    advance(shell, 61, step=30)
    assert view.ring.text() == "59:59"
    assert view._ring_px == 60  # noqa: SLF001


def test_no_project_hides_badge_and_separator(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Inbox", 25)
    pump()
    assert not view.project_badge.isVisibleTo(view)
    assert not view.meta_dot.isVisibleTo(view)
    assert view.meta_label.text().startswith("Ends at ")


def test_ends_at_follows_time_format(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.settings.update(time_format="12h")
    shell.engine.start("Inbox", 150)
    pump()
    assert view.meta_label.text() == "Ends at 12:30 PM"


# --------------------------------------------------------------------------- controls


def test_buttons_call_the_engine(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    eng.start("Write", 30)
    pump()
    view.pause_button.click()
    assert str(eng.state) == "paused"
    view.pause_button.click()
    assert str(eng.state) == "focus"
    view.extend_button.click()
    assert eng.planned_seconds == 35 * 60
    assert view.ring.caption() == "of 35 min"
    advance(shell, 120)
    view.finish_button.click()
    assert str(eng.state) == "idle"
    assert view.mode() == "idle"


def test_finish_under_a_minute_abandons(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    sid = eng.start("Write", 30)
    advance(shell, 20, step=10)
    view.finish_button.click()
    assert shell.repo.get_session(sid) is None


def test_extend_disabled_at_the_eight_hour_cap(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Marathon", 480)
    pump()
    assert not view.extend_button.isEnabled()


def test_paused_visuals(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    eng.start("Write", 30)
    advance(shell, 90, step=30)
    eng.pause()
    pump()
    pal = theme.palette()
    assert view.pause_button.text() == "Resume"
    assert view.pause_button.variant() == "primary"
    assert view.pause_button.icon_name() == "play"
    assert view.ring.caption() == "Paused"
    assert view.ring._color is not None and view.ring._color.name().upper() == pal.text_subtle.upper()  # noqa: SLF001
    assert view.ring._text_color is not None and view.ring._text_color.name().upper() == pal.text_muted.upper()  # noqa: SLF001
    assert view.meta_label.text() == "Paused at 10:01"
    assert view.checkin_strip.status_label.text() == "Check-ins paused"
    assert view.ring.accessibleDescription().startswith("Paused")
    # reduce_motion is on in the shell → no breathing; turning it off starts the breathe.
    assert not view.ring.is_breathing()
    shell.settings.update(reduce_motion=False)
    assert view.ring.is_breathing()
    eng.resume()
    pump()
    assert not view.ring.is_breathing()
    assert view.ring._color is None  # back to the accent  # noqa: SLF001
    assert view.pause_button.text() == "Pause" and view.pause_button.variant() == "secondary"


def test_paused_ring_follows_theme(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Write", 30)
    shell.engine.pause()
    shell.settings.update(theme="light")
    pump()
    assert view.ring._color.name().upper() == theme.palette().text_subtle.upper()  # noqa: SLF001
    assert not theme.palette().is_dark


def test_buttons_never_take_focus_on_click(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Write", 30)
    pump()
    for b in (view.pause_button, view.extend_button, view.finish_button, view.hide_button, view.more_button):
        assert b.focusPolicy() == Qt.FocusPolicy.TabFocus
    view.capture.line_edit.setFocus()
    QTest.mouseClick(view.pause_button, Qt.MouseButton.LeftButton)
    assert str(shell.engine.state) == "paused"
    assert QApplication.focusWidget() is view.capture.line_edit


def test_hide_to_mini_timer(shell: AppContext, view: ActiveView, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    calls: list[int] = []
    monkeypatch.setattr(shell.window, "hide_main", lambda: calls.append(1))
    view.hide_to_mini()
    assert calls == []  # idle: nothing to hide to
    shell.engine.start("Write", 30)
    view.hide_button.click()
    assert calls == [1]
    assert "Ctrl+H" in view.hide_button.toolTip()


def test_abandon_asks_in_a_sheet(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    sid = eng.start("Write", 30)
    advance(shell, 300)
    win = shell.window
    view.confirm_abandon()
    sheet = win.sheet_host.current()  # type: ignore[union-attr]
    assert isinstance(sheet, ConfirmSheet)
    assert "Write" in sheet.text_label.text()
    sheet.reject()
    pump()
    assert eng.is_active
    view.confirm_abandon()
    sheet = win.sheet_host.current()  # type: ignore[union-attr]
    sheet.accept()
    pump()
    assert not eng.is_active
    session = shell.repo.get_session(sid)
    assert session is not None and str(session.outcome) == "abandoned"


def test_more_menu_has_abandon(shell: AppContext, view: ActiveView, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from madhura.ui.widgets import menu as menu_mod

    shown: list[Any] = []
    monkeypatch.setattr(menu_mod.Menu, "popup_below", lambda self, w, align="left", gap=4: shown.append(self))
    shell.engine.start("Write", 30)
    view.more_button.click()
    assert shown and [a.text() for a in shown[0].actions()] == ["Abandon session…"]


# --------------------------------------------------------------------------- capture


def test_capture_saves_and_shows_parked_caption(shell: AppContext, view: ActiveView, qtbot: Any) -> None:  # noqa: F811
    sid = shell.engine.start("Write", 30)
    pump()
    events: list[str] = []
    shell.events.data_changed.connect(events.append)
    view.focus_capture()
    assert QApplication.focusWidget() is view.capture.line_edit
    QTest.keyClicks(view.capture.line_edit, "Call the bank")
    QTest.keyClick(view.capture.line_edit, Qt.Key.Key_Return)
    caps = shell.repo.open_captures()
    assert [(c.text, c.session_id) for c in caps] == [("Call the bank", sid)]
    assert view.capture.text() == ""
    assert "captures" in events
    assert view.parked_label.text() == "✓ Parked · 1 in parking lot"
    assert QApplication.focusWidget() is view.capture.line_edit  # Enter keeps focus
    view._parked_timer.setInterval(30)  # noqa: SLF001
    view._parked_timer.start()  # noqa: SLF001
    qtbot.waitUntil(lambda: view.parked_label.text() == "", timeout=2000)


def test_capture_is_not_autofocused_and_empty_does_nothing(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Write", 30)
    pump()
    assert QApplication.focusWidget() is not view.capture.line_edit
    view.capture.line_edit.setFocus()
    QTest.keyClick(view.capture.line_edit, Qt.Key.Key_Return)
    assert shell.repo.open_captures() == []
    assert view.parked_label.text() == ""


def test_c_key_focuses_capture_and_escape_blurs(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Write", 30)
    pump()
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_C)
    assert QApplication.focusWidget() is view.capture.line_edit
    QTest.keyClick(view.capture.line_edit, Qt.Key.Key_Escape)
    assert QApplication.focusWidget() is view
    assert view.capture.text() == ""


def test_focus_capture_ignored_on_break(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start_break(5)
    view.focus_capture()
    assert QApplication.focusWidget() is not view.capture.line_edit


# --------------------------------------------------------------------------- break view


def test_break_view(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    eng.start_break(5)
    advance(shell, 48, step=12)
    pal = theme.palette()
    assert view.mode() == "break"
    assert view.break_title.isVisibleTo(view) and not view.title.isVisibleTo(view)
    assert view.break_title.text() == "Stand up, stretch, look far away."
    assert view.skip_button.text() == "Skip break"
    assert view.ring.caption() == "Break"
    assert view.ring.text() == "04:12"
    assert view.ring._color.name().upper() == pal.break_.upper()  # noqa: SLF001
    assert not view.capture_box.isVisibleTo(view) and not view.checkin_strip.isVisibleTo(view)
    assert view.next_box.isVisibleTo(view)
    view.skip_button.click()
    assert str(eng.state) == "idle"


def test_break_after_a_project_session_has_no_badge(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.settings.update(auto_start_break=True)
    shell.engine.start("Write", 2, "Work")
    pump()
    assert view.project_badge.isVisibleTo(view)
    advance(shell, 121, step=30)
    assert str(shell.engine.state) == "break" and view.mode() == "break"
    assert not view.project_badge.isVisibleTo(view) and not view.meta_dot.isVisibleTo(view)
    assert view.meta_label.text().startswith("Ends at ")


def test_lunch_break_copy(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start_break(30, "Lunch")
    pump()
    assert view.skip_button.text() == "End lunch"
    assert view.ring.caption() == "Lunch"
    assert view.break_title.text() == av.BREAK_COPY["Lunch"]


def test_next_block_prefill_and_enter_starts(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    repo = shell.repo
    t1 = repo.add_task("Review PR", 45, "Madhura", plan_date=shell.today())
    repo.add_task("Email Priya", 15, plan_date=shell.today())
    shell.engine.start_break(5)
    pump()
    assert view.next_field.text() == "Review PR"
    assert view.next_minutes.text() == "45m"
    QTest.keyClick(view.next_field.line_edit, Qt.Key.Key_Return)
    eng = shell.engine
    assert str(eng.state) == "focus"
    assert (eng.task, eng.planned_seconds, eng.project, eng.planned_task_id) == ("Review PR", 45 * 60, "Madhura", t1)
    assert view.mode() == "focus"


def test_next_block_custom_text_uses_default_minutes(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.repo.add_task("Review PR", 45, plan_date=shell.today())
    shell.engine.start_break(5)
    pump()
    view.next_field.line_edit.selectAll()
    QTest.keyClicks(view.next_field.line_edit, "Sketch the onboarding")
    assert view.next_minutes.text() == "1h"
    view.next_start.click()
    eng = shell.engine
    assert (eng.task, eng.planned_seconds, eng.planned_task_id) == ("Sketch the onboarding", 60 * 60, None)


def test_next_block_empty_shows_error(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start_break(5)
    pump()
    assert view.next_field.text() == ""
    assert view.start_from_input() is None
    assert view.next_field.error_text() == "Name the one thing you'll work on"
    assert str(shell.engine.state) == "break"


def test_next_block_keeps_user_edits_on_task_changes(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start_break(5)
    pump()
    view.next_field.line_edit.setFocus()
    QTest.keyClicks(view.next_field.line_edit, "My own idea")
    shell.repo.add_task("Review PR", 45, plan_date=shell.today())
    shell.events.data_changed.emit("tasks")
    assert view.next_field.text() == "My own idea"


def test_next_block_follows_new_tasks_until_edited(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start_break(5)
    pump()
    shell.repo.add_task("Review PR", 45, plan_date=shell.today())
    shell.events.data_changed.emit("tasks")
    assert view.next_field.text() == "Review PR"


def test_focus_task_input_hook_during_break(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.repo.add_task("Review PR", 45, plan_date=shell.today())
    shell.engine.start_break(5)
    pump()
    view.focus_task_input()
    assert QApplication.focusWidget() is view.next_field.line_edit
    assert view.next_field.line_edit.selectedText() == "Review PR"


def test_next_block_uses_the_logical_day(qapp: QApplication, data_dir: Any, qtbot: Any) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 2, 30))  # 02:30 → still logical Wed 14 Oct
    ctx = make_shell(qapp, data_dir, clock)
    try:
        assert ctx.today() == date(2026, 10, 14)
        ctx.repo.add_task("Late-night fix", 25, plan_date=date(2026, 10, 14))
        ctx.repo.add_task("Tomorrow's thing", 25, plan_date=date(2026, 10, 15))
        v = ActiveView(ctx)
        v.show()
        ctx.engine.start_break(5)
        pump()
        assert v.next_field.text() == "Late-night fix"
        v.hide()
        v.deleteLater()
    finally:
        dispose_shell(ctx, qapp)


# --------------------------------------------------------------------------- inline check-in


def test_inline_prompt_shows_and_f_answers(shell: AppContext, view: ActiveView, qtbot: Any) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write the report", 30)
    pump()
    strip = view.checkin_strip
    assert strip.phase() == "status"
    assert not view._key_focused.isEnabled()  # noqa: SLF001
    ck.prompt_inline.emit()
    assert strip.phase() == "prompt"
    assert strip.frame.isVisibleTo(view) and not strip.status_label.isVisibleTo(view)
    assert strip.question_label.text() == "Still on it?"
    assert view._key_focused.isEnabled() and view._key_back.isEnabled()  # noqa: SLF001
    assert strip.bar.isVisibleTo(view) and abs(strip.bar.fraction() - 0.8) < 1e-6
    QTest.keyClick(view, Qt.Key.Key_F)
    assert ck.answers == [("focused", None)]
    assert strip.phase() == "confirm"
    assert strip.confirm_label.text() == "✓ Nice — next check-in in ~7 min"
    assert not view._key_focused.isEnabled()  # noqa: SLF001
    strip._confirm_timer.setInterval(20)  # noqa: SLF001
    strip._confirm_timer.start()  # noqa: SLF001
    qtbot.waitUntil(lambda: strip.phase() == "status", timeout=2000)
    assert strip.status_label.text() == "Check-ins: 1 focused · next in ~7 min"


def test_inline_confirmation_never_promises_a_check_in_that_cannot_fire(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    """25-min block answered at 21:00 with the next check-in 7 min away: only 4 min are left,
    so — like the popup and the status line — no "next check-in" is promised."""
    ck = fake(shell)
    shell.settings.update(checkin_enabled=False)  # the real service (still alive) stays quiet
    shell.engine.start("Write the report", 25)
    advance(shell, 21 * 60, step=30)
    assert 230 < shell.engine.remaining_seconds <= 240
    ck.prompt_inline.emit()
    view.checkin_strip.answer_focused()
    assert view.checkin_strip.confirm_label.text() == "✓ Nice — keep going"
    view.checkin_strip._end_confirm()  # noqa: SLF001
    # Plenty of block left → announced as before.
    ck.next_in = 120.0
    ck.prompt_inline.emit()
    view.checkin_strip.answer_focused()
    assert view.checkin_strip.confirm_label.text() == "✓ Nice — next check-in in ~2 min"


def test_first_prompt_ever_shows_the_intro_once(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    assert view.checkin_strip.intro_label.isVisibleTo(view)
    assert shell.repo.kv_get("checkin_intro_seen") is True
    view.checkin_strip.answer_focused()
    view.checkin_strip._end_confirm()  # noqa: SLF001
    ck.prompt_inline.emit()
    assert not view.checkin_strip.intro_label.isVisibleTo(view)


def test_b_shows_reasons_and_a_chip_logs_drifted(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write the report", 30)
    ck.prompt_inline.emit()
    QTest.keyClick(view, Qt.Key.Key_B)
    strip = view.checkin_strip
    assert strip.phase() == "reason"
    assert [c.text() for c in strip.reason_chips] == list(av.DRIFT_REASONS)
    assert strip.reason_label.text() == "What pulled you? (optional)"
    assert ck.answers == []
    QTest.keyClick(view, Qt.Key.Key_F)  # keys only answer the question, not the reasons
    assert ck.answers == []
    strip.reason_chips[1].click()
    assert ck.answers == [("drifted", "Social media")]
    assert strip.confirm_label.text() == "Back to Write the report."


def test_reason_times_out_after_six_seconds_without_reason(shell: AppContext, view: ActiveView, qtbot: Any) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    strip = view.checkin_strip
    assert strip._reason_timer.interval() == 6000  # noqa: SLF001
    strip._reason_timer.setInterval(30)  # noqa: SLF001
    strip.back_button.click()
    assert strip.phase() == "reason"
    qtbot.waitUntil(lambda: ck.answers == [("drifted", None)], timeout=2000)


def test_focusing_back_with_little_time_left_logs_immediately(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    ck.progress = 0.05  # 3 s of the 60 s timeout left
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    view.checkin_strip.back_button.click()
    assert ck.answers == [("drifted", None)]


def test_focusing_back_freezes_the_service_timeout_when_supported(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    ck.progress = 0.05  # would log at once without begin_reason()
    calls: list[int] = []
    ck.begin_reason = lambda: calls.append(1)  # type: ignore[attr-defined]
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    view.checkin_strip.back_button.click()
    assert calls == [1]
    assert view.checkin_strip.phase() == "reason" and ck.answers == []


def test_real_service_inline_round_trip(shell: AppContext) -> None:  # noqa: F811
    """End to end with the CheckInService [O] ships (guarded): the Focus page's view prompts
    inline, F records 'focused', B + a chip records 'drifted' with the reason."""
    ck = shell.checkins
    if ck is None or not callable(getattr(ck, "fire_now", None)):
        pytest.skip("check-in service without fire_now()")
    win = shell.window
    page = win.pages["focus"]  # type: ignore[union-attr]
    view = getattr(page, "active_view", None)
    if not isinstance(view, ActiveView):
        pytest.skip("FocusPage does not embed the ActiveView")
    win.show_main("focus")  # type: ignore[union-attr]
    pump(10)
    if not win.is_main_active():  # type: ignore[union-attr]
        pytest.skip("window not active on this platform")
    eng = shell.engine
    sid = eng.start("Write the report", 45)
    pump(10)
    assert ck.fire_now()
    assert view.checkin_strip.phase() == "prompt"
    view.checkin_strip.answer_focused()
    assert [str(c.response) for c in shell.repo.checkins_for_session(sid)] == ["focused"]
    view.checkin_strip._end_confirm()  # noqa: SLF001
    assert ck.fire_now()
    view.checkin_strip.answer_back()
    assert view.checkin_strip.phase() == "reason"
    view.checkin_strip.reason_chips[0].click()
    got = [(str(c.response), c.reason) for c in shell.repo.checkins_for_session(sid)]
    assert got == [("focused", None), ("drifted", "Phone")]
    assert view.checkin_strip.confirm_label.text() == "Back to Write the report."


def test_prompt_closed_hides_without_recording(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    ck.prompt_closed.emit()
    assert view.checkin_strip.phase() == "status"
    assert ck.answers == []
    QTest.keyClick(view, Qt.Key.Key_F)
    assert ck.answers == []


def test_pausing_closes_the_prompt_and_reason_step_without_record(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    eng = shell.engine
    eng.start("Write", 30)
    ck.prompt_inline.emit()
    view.checkin_strip.answer_back()
    assert view.checkin_strip.phase() == "reason"
    eng.pause()
    assert view.checkin_strip.phase() == "status"
    assert not view.checkin_strip._reason_timer.isActive()  # noqa: SLF001
    assert ck.answers == []


def test_prompt_ignored_unless_focusing(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start_break(5)
    ck.prompt_inline.emit()
    assert view.checkin_strip.phase() == "status"


def test_answered_elsewhere_cancels_inline_prompt(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    ck.answered.emit("focused")
    assert view.checkin_strip.phase() == "status"


def test_keys_disabled_while_a_sheet_is_open(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    shell.engine.start("Write", 30)
    ck.prompt_inline.emit()
    shell.window.show_sheet(ConfirmSheet("Hi"))  # type: ignore[union-attr]
    assert not view._key_focused.isEnabled()  # noqa: SLF001
    view._on_key_focused()  # noqa: SLF001
    assert ck.answers == []
    shell.window.close_sheet()  # type: ignore[union-attr]
    pump()
    assert view._key_focused.isEnabled()  # noqa: SLF001


def test_strip_status_with_counts(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    ck = fake(shell)
    ck.next_in = 250.0
    shell.engine.start("Write", 45)
    ck._counts.update(focused=3, drifted=1)  # noqa: SLF001
    shell.events.data_changed.emit("checkins")
    assert view.checkin_strip.status_label.text() == "Check-ins: 3 focused · 1 came back · next in ~5 min"
    shell.settings.update(checkin_enabled=False)
    assert view.checkin_strip.status_label.text() == "Check-ins off"


def test_view_binds_to_the_service_created_after_it(shell: AppContext, qtbot: Any) -> None:  # noqa: F811
    """The real app builds the window before start_services(): binding must be lazy."""
    real = shell.checkins
    shell.checkins = None
    v = ActiveView(shell)
    v.show()
    replacement = FakeCheckins()
    shell.checkins = replacement  # type: ignore[assignment]
    shell.engine.start("Write", 30)
    replacement.prompt_inline.emit()
    assert v.checkin_strip.phase() == "prompt"
    v.hide()
    v.deleteLater()
    shell.checkins = real


def test_page_view_follows_the_real_checkin_service(shell: AppContext) -> None:  # noqa: F811
    """Guarded integration with whatever CheckInService [O] ships: its prompt_inline drives the
    Focus page's ActiveView."""
    ck = shell.checkins
    if ck is None or not hasattr(ck, "prompt_inline"):
        pytest.skip("no check-in service")
    page = shell.window.pages["focus"]  # type: ignore[union-attr]
    view = getattr(page, "active_view", None)
    if not isinstance(view, ActiveView):
        pytest.skip("FocusPage does not embed the ActiveView")
    shell.engine.start("Write", 30)
    view.refresh()
    ck.prompt_inline.emit()
    assert view.checkin_strip.phase() == "prompt"
    ck.prompt_closed.emit()
    assert view.checkin_strip.phase() == "status"


# --------------------------------------------------------------------------- motion


def test_start_sweep_and_jump_tween(shell: AppContext, view: ActiveView, qtbot: Any) -> None:  # noqa: F811
    shell.settings.update(reduce_motion=False)
    eng = shell.engine
    eng.start("Write", 30)
    assert view._sweep_pending  # noqa: SLF001
    qtbot.waitUntil(lambda: not view._sweep_pending, timeout=1000)  # noqa: SLF001
    assert abs(view.ring.progress() - 1.0) < 1e-3
    assert view.ring.displayed_progress() < 0.999  # sweeping 0 → full
    qtbot.waitUntil(lambda: view.ring.displayed_progress() == view.ring.progress(), timeout=2000)
    advance(shell, 600, step=1)  # real ticks: one second each → no tween, the ring follows
    assert view.ring.displayed_progress() == pytest.approx(view.ring.progress(), abs=1e-4)
    before = view.ring.progress()
    eng.extend(5)
    after = view.ring.progress()
    assert after > before + 0.04  # 20/30 → 25/35: a jump → tween towards the new target
    assert view.ring.displayed_progress() < after
    qtbot.waitUntil(lambda: abs(view.ring.displayed_progress() - view.ring.progress()) < 1e-3, timeout=2000)


def test_reduce_motion_sets_the_ring_instantly(shell: AppContext, view: ActiveView, qtbot: Any) -> None:  # noqa: F811
    eng = shell.engine
    eng.start("Write", 30)
    qtbot.waitUntil(lambda: not view._sweep_pending, timeout=1000)  # noqa: SLF001
    assert view.ring.displayed_progress() == pytest.approx(1.0, abs=1e-3)
    advance(shell, 600)
    eng.extend(5)
    assert view.ring.displayed_progress() == pytest.approx(view.ring.progress(), abs=1e-4)


def test_restored_session_gets_no_sweep(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    eng = shell.engine
    eng.start("Write", 30)
    pump()
    assert not view._sweep_pending  # noqa: SLF001
    view._session_id = None  # noqa: SLF001 - as if this session came back from restore()
    advance(shell, 60)
    eng.pause()
    assert not view._sweep_pending  # noqa: SLF001


def test_tick_without_change_keeps_text(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    shell.engine.start("Write", 30)
    pump()
    text = view.ring.text()
    view._on_tick()  # noqa: SLF001
    assert view.ring.text() == text


# --------------------------------------------------------------------------- title clamp


def test_title_wraps_balanced_and_clamps_to_two_lines(qapp: QApplication) -> None:
    label = ClampedText("h1", 2)
    label.resize(560, 80)
    label.set_text("Draft the launch announcement for the new pricing page")
    lines = label.lines(560)
    assert len(lines) == 2 and not label.is_clamped()
    assert abs(len(lines[0]) - len(lines[1])) < 15  # balanced: no one-word widow
    label.set_text(" ".join(["Refactor the session repository layer"] * 6))
    lines = label.lines(560)
    assert len(lines) == 2 and lines[-1].endswith("…") and label.is_clamped()
    assert label.toolTip() == label.text()
    label.set_text("https://example.com/" + "a" * 200)  # unbreakable → wraps anywhere
    assert len(label.lines(300)) == 2
    assert label.heightForWidth(560) == 2 * label._line_height()  # noqa: SLF001
    label.set_text("Short")
    assert label.lines(560) == ["Short"] and label.toolTip() == ""


def test_strip_is_a_public_widget(shell: AppContext, view: ActiveView) -> None:  # noqa: F811
    assert isinstance(view.checkin_strip, CheckInStrip)


def test_shortcut_tooltips_use_the_platform_modifier(shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """QShortcut("Ctrl+H") is ⌘H on macOS: the tooltips say so (as the shortcuts sheet does)."""
    from types import SimpleNamespace

    from madhura.ui import shortcuts_sheet

    monkeypatch.setattr(shortcuts_sheet, "sys", SimpleNamespace(platform="darwin"))
    v = ActiveView(shell)
    try:
        assert v.finish_button.toolTip() == "Finish · ⌘+Shift+F"
        assert v.hide_button.toolTip() == "Hide to mini timer · ⌘+H"
        assert v.break_hide_button.toolTip() == "Hide to mini timer · ⌘+H"
        assert v.hide_button.accessibleName() == "Hide to mini timer"
    finally:
        v.deleteLater()
    monkeypatch.setattr(shortcuts_sheet, "sys", SimpleNamespace(platform="win32"))
    v = ActiveView(shell)
    assert v.hide_button.toolTip() == "Hide to mini timer · Ctrl+H"
    v.deleteLater()
