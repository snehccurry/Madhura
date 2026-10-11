"""[P2] Session end: toasts, SessionCompleteSheet, CompletionPanel, pending sheet (spec §1.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_shell_support import advance, shell  # noqa: F401

from madhura.context import AppContext, FakeWindowController
from madhura.ui.overlays import session_complete as sc
from madhura.ui.overlays.session_complete import (
    CompletionPanel,
    SessionCompleteSheet,
    present_session_complete,
    show_pending_complete,
)
from madhura.ui.widgets.popup import FloatingPanel, PopupStack

T0 = datetime(2026, 10, 14, 9, 0)


# --------------------------------------------------------------------------- helpers


def pump(n: int = 5) -> None:
    for _ in range(n):
        QApplication.processEvents()


def make_session(
    ctx: AppContext,
    *,
    task: str = "Write the report",
    project: str | None = "Work",
    planned_min: int = 45,
    focused_s: int | None = None,
    outcome: str = "completed",
    task_id: int | None = None,
    checkins: tuple[str, ...] = (),
    note: str | None = None,
) -> int:
    repo = ctx.repo
    sid = repo.create_session(task, project, task_id, T0, planned_min * 60)
    focused = planned_min * 60 if focused_s is None else focused_s
    repo.update_session(sid, focused_seconds=focused, outcome=outcome, ended_at=datetime(2026, 10, 14, 9, focused // 60 % 60), note=note)
    for i, resp in enumerate(checkins):
        repo.add_checkin(sid, datetime(2026, 10, 14, 9, i + 1), resp, "Phone" if resp == "drifted" else None)
    return sid


@pytest.fixture
def sounds(shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # noqa: F811
    played: list[str] = []
    monkeypatch.setattr(shell.sound, "play", lambda event: played.append(event) or True)
    return played


@pytest.fixture
def active(shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> AppContext:  # noqa: F811
    """The real main window, shown and reporting itself active."""
    win = shell.window
    win.show_main()  # type: ignore[union-attr]
    pump()
    monkeypatch.setattr(win, "is_main_active", lambda: True)
    return shell


@pytest.fixture
def inactive(shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> AppContext:  # noqa: F811
    """The main window hidden (to the tray) — completion goes to the panel."""
    monkeypatch.setattr(shell.window, "is_main_active", lambda: False)
    return shell


def current_sheet(ctx: AppContext) -> Any:
    return ctx.window.sheet_host.current()  # type: ignore[union-attr]


def panel_of(ctx: AppContext) -> CompletionPanel | None:
    return sc._PANELS.get(ctx)  # noqa: SLF001


# --------------------------------------------------------------------------- pure helpers


def test_text_helpers(shell: AppContext) -> None:  # noqa: F811
    sid = make_session(shell, planned_min=45, focused_s=32 * 60 + 59, outcome="ended_early")
    assert sc.minutes_summary(shell.repo.get_session(sid)) == "32 of 45 min"
    assert sc.score_summary({"focused": 6, "drifted": 1, "missed": 2}) == ("6 of 7 focused", "2 unanswered")
    assert sc.score_summary({"focused": 0, "drifted": 0, "missed": 1}) == (None, "1 unanswered")
    assert sc.score_summary({}) == (None, None)
    assert sc.break_choices(5) == [5, 10, 15]
    assert sc.break_choices(7) == [5, 7, 10, 15]
    assert sc.break_choices(10) == [5, 10, 15]


# --------------------------------------------------------------------------- toasts


def test_restored_toast_no_sheet_no_sound(active: AppContext, sounds: list[str]) -> None:
    sid = make_session(active, task="Write the report")
    present_session_complete(active, sid, "completed", True)
    text = active.window.toasts.text()  # type: ignore[union-attr]
    assert text.startswith("Your session on ‘Write") and text.endswith("’ finished while Madhura was closed.")
    assert "…" not in text.split("’")[1]  # the sentence itself is never cut, only the task
    long = sc.restored_text("Refactor the session repository layer and the export pipeline")
    assert long.startswith("Your session on ‘Refactor") and long.endswith("…’ finished while Madhura was closed.")
    assert sc.restored_text("") == "Your last session finished while Madhura was closed."
    assert not active.window.has_sheet()  # type: ignore[union-attr]
    assert sounds == []
    assert panel_of(active) is None


def test_abandoned_toasts(active: AppContext, sounds: list[str]) -> None:
    sid = make_session(active, outcome="abandoned", focused_s=300)
    present_session_complete(active, sid, "abandoned", False)
    assert active.window.toasts.text() == "Session discarded"  # type: ignore[union-attr]
    present_session_complete(active, 99999, "abandoned", False)
    assert active.window.toasts.text() == "Too short to save"  # type: ignore[union-attr]
    assert sounds == [] and not active.window.has_sheet()  # type: ignore[union-attr]


def test_abandon_through_the_engine_reports_too_short(active: AppContext) -> None:
    """End to end via FocusPage's session_finished routing: < 60 s → the row is deleted."""
    eng = active.engine
    eng.start("Write", 30)
    advance(active, 20, step=10)
    eng.abandon()
    assert active.window.toasts.text() == "Too short to save"  # type: ignore[union-attr]


def test_toast_without_a_window_goes_to_the_event_bus(shell: AppContext) -> None:  # noqa: F811
    real = shell.window
    shell.window = None
    try:
        seen: list[tuple[str, str]] = []
        shell.events.toast.connect(lambda text, kind: seen.append((text, kind)))
        present_session_complete(shell, 4242, "abandoned", False)
        assert seen == [("Too short to save", "info")]
    finally:
        shell.window = real


# --------------------------------------------------------------------------- sheet vs panel


def test_active_window_gets_the_sheet_and_sound(active: AppContext, sounds: list[str]) -> None:
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    assert isinstance(sheet, SessionCompleteSheet) and sheet.session_id == sid
    assert sheet.title_text() == "Session complete"
    assert sounds == ["complete"]
    assert panel_of(active) is None
    assert active.repo.kv_get(sc.PENDING_KEY) == sid  # until closed: a quit brings it back
    present_session_complete(active, sid, "completed", False)  # duplicate → same sheet
    assert active.window.sheet_host.queued() == []  # type: ignore[union-attr]
    sheet.done_button.click()
    pump()
    assert active.repo.kv_get(sc.PENDING_KEY) is None


def test_inactive_window_gets_the_panel_and_a_pending_sheet(inactive: AppContext, sounds: list[str]) -> None:
    sid = make_session(inactive)
    present_session_complete(inactive, sid, "completed", False)
    panel = panel_of(inactive)
    assert isinstance(panel, CompletionPanel) and isinstance(panel, FloatingPanel)
    assert panel.kind == "completion" and panel.priority == 0
    assert PopupStack.instance().contains(panel)
    assert not inactive.window.has_sheet()  # type: ignore[union-attr]
    assert inactive.repo.kv_get(sc.PENDING_KEY) == sid
    assert sounds == ["complete"]
    assert panel.title_label.text() == "Nice — 45 min on Write the report"
    assert 119_000 < panel.auto_hide_remaining_ms() <= 121_000


def test_session_end_through_the_engine_picks_sheet_or_panel(active: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    eng = active.engine
    eng.start("Write", 2)
    advance(active, 121, step=30)
    assert isinstance(current_sheet(active), SessionCompleteSheet)
    active.window.close_sheet()  # type: ignore[union-attr]
    pump()
    monkeypatch.setattr(active.window, "is_main_active", lambda: False)
    eng.start("Read", 2)
    advance(active, 121, step=30)
    assert panel_of(active) is not None and panel_of(active).session_id == eng_last_id(active)  # type: ignore[union-attr]


def eng_last_id(ctx: AppContext) -> int:
    return max(s.id for s in ctx.repo.recent_sessions())


def test_ended_early_without_checkins_or_task(active: AppContext) -> None:
    sid = make_session(active, project=None, planned_min=30, focused_s=12 * 60 + 30, outcome="ended_early")
    present_session_complete(active, sid, "ended_early", False)
    sheet = current_sheet(active)
    assert sheet.title_text() == "Session ended"
    assert sheet.summary_label.text().startswith("12 of 30 min")
    assert sheet.timeline is None and sheet.mark_done is None and sheet.project_badge is None


# --------------------------------------------------------------------------- sheet content


def test_sheet_timeline_and_score(active: AppContext) -> None:
    sid = make_session(active, checkins=("focused", "focused", "drifted", "focused", "missed", "focused", "focused"))
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    assert sheet.timeline is not None
    assert sheet.timeline.responses() == ["focused", "focused", "drifted", "focused", "missed", "focused", "focused"]
    assert len(sheet.timeline.segment_rects()) == 7
    assert all(r.height() == 8 for r in sheet.timeline.segment_rects())
    assert sheet.score_label.text() == "5 of 6 focused · 1 unanswered"
    assert sheet.summary_label.text() == "45 of 45 min · 09:00–09:45"
    assert sheet.project_badge is not None and sheet.project_badge.text() == "Work"
    assert sheet.timeline.accessibleDescription() == "5 focused, 1 came back, 1 unanswered"


@pytest.mark.parametrize(("outcome", "checked"), [("completed", True), ("ended_early", False)])
def test_mark_done_default_follows_outcome(active: AppContext, outcome: str, checked: bool) -> None:
    tid = active.repo.add_task("Review PR", 45, plan_date=active.today())
    sid = make_session(active, task="Review PR", task_id=tid, outcome=outcome, focused_s=None if outcome == "completed" else 600)
    sheet = SessionCompleteSheet(active, sid, outcome)
    assert sheet.mark_done is not None
    assert sheet.mark_done.text() == "Mark ‘Review PR’ done"
    assert sheet.mark_done.isChecked() is checked
    events: list[str] = []
    active.events.data_changed.connect(events.append)
    active.window.show_sheet(sheet)  # type: ignore[union-attr]
    sheet.done_button.click()
    pump()
    assert bool(active.repo.get_task(tid).done) is checked
    assert ("tasks" in events) is checked


def test_mark_done_can_be_unchecked_and_respects_already_done(active: AppContext) -> None:
    tid = active.repo.add_task("Review PR", 45, plan_date=active.today())
    active.repo.set_task_done(tid, True)
    sid = make_session(active, task="Review PR", task_id=tid, outcome="ended_early", focused_s=600)
    sheet = SessionCompleteSheet(active, sid, "ended_early")
    assert sheet.mark_done.isChecked()  # already done in Up next
    active.window.show_sheet(sheet)  # type: ignore[union-attr]
    sheet.mark_done.click()
    sheet.close_sheet()
    pump()
    assert not active.repo.get_task(tid).done


def test_note_saved_on_done_and_on_escape(active: AppContext) -> None:
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    sheet.note.set_text("  Good flow after the first 10 min  ")
    sheet.done_button.click()
    pump()
    assert active.repo.get_session(sid).note == "Good flow after the first 10 min"
    sid2 = make_session(active, task="Second")
    present_session_complete(active, sid2, "completed", False)
    pump()
    sheet2 = current_sheet(active)
    assert sheet2.session_id == sid2
    sheet2.note.setFocus()
    sheet2.note.set_text("Esc keeps it")
    QTest.keyClick(sheet2.note, Qt.Key.Key_Escape)
    pump()
    assert not active.window.has_sheet()  # type: ignore[union-attr]
    assert active.repo.get_session(sid2).note == "Esc keeps it"


def test_existing_note_is_prefilled_and_cleared_note_saved_as_none(active: AppContext) -> None:
    sid = make_session(active, note="old")
    sheet = SessionCompleteSheet(active, sid, "completed")
    assert sheet.note.text() == "old"
    active.window.show_sheet(sheet)  # type: ignore[union-attr]
    sheet.note.set_text("   ")
    sheet.close_sheet()
    pump()
    assert active.repo.get_session(sid).note is None


def test_keep_going_starts_same_task_and_skips_mark_done(active: AppContext) -> None:
    tid = active.repo.add_task("Review PR", 45, "Madhura", plan_date=active.today())
    sid = make_session(active, task="Review PR", project="Madhura", task_id=tid)
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    assert sheet.keep_button.text() == "Keep going +15"
    sheet.keep_button.click()
    pump()
    eng = active.engine
    assert str(eng.state) == "focus"
    assert (eng.task, eng.planned_seconds, eng.project, eng.planned_task_id) == ("Review PR", 900, "Madhura", tid)
    assert not active.repo.get_task(tid).done  # continuing ≠ done


def test_break_chips_drive_the_primary(active: AppContext) -> None:
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    assert sheet.break_row.isVisibleTo(sheet)
    assert [v for v, _l in sheet.break_chips.options()] == [5, 10, 15]
    assert sheet.break_chips.value() == 5
    assert sheet.main_button.text() == "Take a 5-min break"
    sheet.break_chips._activate(1)  # noqa: SLF001 - user picks "10 min"
    assert sheet.main_button.text() == "Take a 10-min break"
    sheet.main_button.click()
    pump()
    eng = active.engine
    assert str(eng.state) == "break" and eng.break_total_seconds == 600
    assert not active.window.has_sheet()  # type: ignore[union-attr]


def test_ctrl_enter_triggers_the_primary(active: AppContext) -> None:
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    pump(10)
    sheet = current_sheet(active)
    sheet.note.setFocus()
    QTest.keyClick(sheet.note, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    pump()
    assert str(active.engine.state) == "break" and active.engine.break_total_seconds == 300


def test_breaks_disabled_offers_plan_next(active: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    active.settings.update(breaks_enabled=False)
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    assert sheet.main_button.text() == "Plan next"
    assert not sheet.break_row.isVisibleTo(sheet)
    calls: list[str] = []
    monkeypatch.setattr(active.window, "focus_task_input", lambda: calls.append("focus"))
    sheet.main_button.click()
    pump()
    assert calls == ["focus"]
    assert active.window.current_page() == "focus"  # type: ignore[union-attr]
    assert str(active.engine.state) == "idle"


def test_auto_break_running_shows_done_and_keep_going(active: AppContext) -> None:
    active.settings.update(auto_start_break=True)
    eng = active.engine
    eng.start("Write", 2)
    advance(active, 121, step=30)
    assert str(eng.state) == "break"
    sheet = current_sheet(active)
    assert isinstance(sheet, SessionCompleteSheet)
    assert not sheet.main_button.isVisibleTo(sheet)
    assert not sheet.break_row.isVisibleTo(sheet)
    assert sheet.keep_button.isVisibleTo(sheet)
    assert sheet.done_button.variant() == "primary"
    assert sheet.primary_button() is sheet.done_button  # Ctrl+Enter target
    sheet.keep_button.click()
    pump()
    assert str(eng.state) == "focus" and eng.planned_seconds == 900


def test_sheet_while_a_session_runs_offers_only_done(active: AppContext) -> None:
    sid = make_session(active)
    active.engine.start("Something else", 25)
    sheet = SessionCompleteSheet(active, sid, "completed")
    active.window.show_sheet(sheet)  # type: ignore[union-attr]
    assert not sheet.keep_button.isVisibleTo(sheet)
    assert not sheet.main_button.isVisibleTo(sheet)
    assert not sheet.break_row.isVisibleTo(sheet)
    assert sheet.done_button.variant() == "primary"
    active.engine.abandon()  # session ends while the sheet is open → actions come back
    assert sheet.main_button.isVisibleTo(sheet)


def test_check_draws_itself(active: AppContext, qtbot: Any) -> None:
    sid = make_session(active)
    sheet = SessionCompleteSheet(active, sid, "completed")
    sheet.check.play()
    assert sheet.check.progress() == 1.0  # reduce_motion → drawn at once
    active.settings.update(reduce_motion=False)
    sheet.check.show()
    sheet.check.play()
    assert sheet.check.progress() < 1.0
    qtbot.waitUntil(lambda: sheet.check.progress() == 1.0, timeout=2000)


def test_sheet_with_missing_session_is_harmless(active: AppContext) -> None:
    sheet = SessionCompleteSheet(active, 31337, "completed")
    active.window.show_sheet(sheet)  # type: ignore[union-attr]
    assert not sheet.keep_button.isVisibleTo(sheet)
    sheet.close_sheet()
    pump()


# --------------------------------------------------------------------------- panel


def test_panel_actions(inactive: AppContext) -> None:
    sid = make_session(inactive, checkins=("focused", "drifted"))
    present_session_complete(inactive, sid, "completed", False)
    panel = panel_of(inactive)
    assert panel.score_label.text() == "1 of 2 focused"
    assert panel.main_button.text() == "Take a 5-min break"
    assert panel.keep_button.text() == "Keep going +15"
    assert panel.done_button.accessibleName() == "Done" and panel.done_button.toolTip() == "Done"
    for b in (panel.main_button, panel.keep_button, panel.done_button):
        assert b.focusPolicy() == Qt.FocusPolicy.NoFocus
    panel.main_button.click()
    pump()
    assert str(inactive.engine.state) == "break" and inactive.engine.break_total_seconds == 300
    assert panel_of(inactive) is None
    assert inactive.repo.kv_get(sc.PENDING_KEY) == sid  # note / mark-done still to come


def test_panel_keep_going_clears_pending(inactive: AppContext) -> None:
    sid = make_session(inactive, task="Write", project="Work")
    present_session_complete(inactive, sid, "completed", False)
    panel_of(inactive).keep_button.click()
    pump()
    eng = inactive.engine
    assert (eng.task, eng.planned_seconds, eng.project) == ("Write", 900, "Work")
    assert inactive.repo.kv_get(sc.PENDING_KEY) is None


def test_panel_plan_next_when_breaks_off(inactive: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    inactive.settings.update(breaks_enabled=False)
    sid = make_session(inactive)
    present_session_complete(inactive, sid, "ended_early", False)
    panel = panel_of(inactive)
    assert panel.main_button.text() == "Plan next"
    assert panel.score_label.text() == "Ended early · 45 of 45 min"
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(inactive.window, "show_main", lambda page=None, activate=True: calls.append(("show", page, activate)))
    monkeypatch.setattr(inactive.window, "focus_task_input", lambda: calls.append(("focus", None)))
    panel.main_button.click()
    assert calls == [("show", "focus", True), ("focus", None)]


def test_panel_done_and_auto_hide(inactive: AppContext, qtbot: Any) -> None:
    sid = make_session(inactive)
    present_session_complete(inactive, sid, "completed", False)
    panel = panel_of(inactive)
    closed: list[int] = []
    panel.closed.connect(lambda: closed.append(1))
    panel._auto_hide.setInterval(20)  # noqa: SLF001
    panel._auto_hide.start()  # noqa: SLF001
    qtbot.waitUntil(lambda: closed == [1], timeout=2000)
    assert panel_of(inactive) is None
    sid2 = make_session(inactive, task="Two")
    present_session_complete(inactive, sid2, "completed", False)
    panel2 = panel_of(inactive)
    panel2.done_button.click()
    qtbot.waitUntil(lambda: panel_of(inactive) is None, timeout=2000)


def test_panel_hover_pauses_auto_hide(inactive: AppContext) -> None:
    sid = make_session(inactive)
    present_session_complete(inactive, sid, "completed", False)
    panel = panel_of(inactive)
    panel.hover_changed.emit(True)
    assert panel.auto_hide_remaining_ms() == -1
    panel.hover_changed.emit(False)
    assert panel.auto_hide_remaining_ms() > 100_000


def test_new_panel_replaces_the_old_one(inactive: AppContext) -> None:
    a = make_session(inactive, task="A")
    b = make_session(inactive, task="B")
    present_session_complete(inactive, a, "completed", False)
    first = panel_of(inactive)
    present_session_complete(inactive, b, "completed", False)
    second = panel_of(inactive)
    assert second is not first and second.session_id == b
    assert not PopupStack.instance().contains(first)
    assert inactive.repo.kv_get(sc.PENDING_KEY) == b


def test_panel_with_auto_break_running(inactive: AppContext) -> None:
    inactive.settings.update(auto_start_break=True)
    eng = inactive.engine
    eng.start("Write", 2)
    advance(inactive, 121, step=30)
    panel = panel_of(inactive)
    assert panel is not None
    assert not panel.main_button.isVisibleTo(panel)
    assert panel.keep_button.isVisibleTo(panel)
    assert panel.score_label.text() == "5-min break started"
    assert panel.title_label.text() == "Nice — 2 min on Write"


# --------------------------------------------------------------------------- pending sheet


def test_pending_round_trip(inactive: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = inactive.repo.add_task("Review PR", 45, plan_date=inactive.today())
    sid = make_session(inactive, task="Review PR", task_id=tid)
    present_session_complete(inactive, sid, "completed", False)
    assert inactive.repo.kv_get(sc.PENDING_KEY) == sid
    assert show_pending_complete(inactive) is None  # window hidden → wait
    win = inactive.window
    win.show_main()  # type: ignore[union-attr]
    pump(10)
    sheet = current_sheet(inactive)
    assert isinstance(sheet, SessionCompleteSheet) and sheet.pending and sheet.session_id == sid
    assert panel_of(inactive) is None  # the panel made way for the sheet
    assert show_pending_complete(inactive) is sheet  # idempotent
    assert win.sheet_host.queued() == []  # type: ignore[union-attr]
    sheet.note.set_text("From the pending sheet")
    sheet.done_button.click()
    pump()
    assert inactive.repo.kv_get(sc.PENDING_KEY) is None
    assert inactive.repo.get_session(sid).note == "From the pending sheet"
    assert inactive.repo.get_task(tid).done
    assert show_pending_complete(inactive) is None


def test_pending_shows_on_window_activation(inactive: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = make_session(inactive)
    win = inactive.window
    monkeypatch.setattr(win, "is_main_visible", lambda: False)
    present_session_complete(inactive, sid, "completed", False)
    pump()
    assert not win.has_sheet()  # type: ignore[union-attr]
    monkeypatch.setattr(win, "is_main_visible", lambda: True)
    QApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    pump()
    assert isinstance(current_sheet(inactive), SessionCompleteSheet)


def test_pending_at_startup_via_show_pending_complete(shell: AppContext) -> None:  # noqa: F811
    sid = make_session(shell)
    shell.repo.kv_set(sc.PENDING_KEY, sid)
    real, fake = shell.window, FakeWindowController()
    shell.window = fake  # type: ignore[assignment]
    try:
        sheet = show_pending_complete(shell)
        assert isinstance(sheet, SessionCompleteSheet) and fake.sheets == [sheet]
        fake.close_sheet()
        assert shell.repo.kv_get(sc.PENDING_KEY) is None
    finally:
        shell.window = real


@pytest.mark.parametrize("value", [123456, "junk", True])
def test_stale_or_invalid_pending_is_cleared(shell: AppContext, value: Any) -> None:  # noqa: F811
    shell.window.show_main()  # type: ignore[union-attr]
    shell.repo.kv_set(sc.PENDING_KEY, value)
    assert show_pending_complete(shell) is None
    assert shell.repo.kv_get(sc.PENDING_KEY) is None


def test_pending_for_abandoned_session_is_dropped(shell: AppContext) -> None:  # noqa: F811
    shell.window.show_main()  # type: ignore[union-attr]
    sid = make_session(shell, outcome="abandoned", focused_s=300)
    shell.repo.kv_set(sc.PENDING_KEY, sid)
    assert show_pending_complete(shell) is None
    assert shell.repo.kv_get(sc.PENDING_KEY) is None


def test_fake_window_controller_decision(shell: AppContext) -> None:  # noqa: F811
    sid = make_session(shell)
    real, fake = shell.window, FakeWindowController(visible=True, active=True)
    shell.window = fake  # type: ignore[assignment]
    try:
        present_session_complete(shell, sid, "completed", False)
        assert len(fake.sheets) == 1 and isinstance(fake.sheets[0], SessionCompleteSheet)
        fake.active = False
        sid2 = make_session(shell, task="Two")
        present_session_complete(shell, sid2, "completed", False)
        assert len(fake.sheets) == 1 and panel_of(shell) is not None
        panel_of(shell).dismiss(animate=False)
    finally:
        shell.window = real


def test_card_refits_when_actions_change(active: AppContext) -> None:
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    pump()
    host = active.window.sheet_host  # type: ignore[union-attr]
    full = host.card_geometry().height()
    active.engine.start("Something else", 25)  # footer shrinks to "Done"
    pump()
    assert host.card_geometry().height() < full - 30


# --------------------------------------------------------------------------- quit / logoff with a sheet open


def test_quit_or_logoff_saves_the_note_and_keeps_the_sheet_pending(active: AppContext) -> None:
    """flush_data (commitDataRequest, and the first step of shutdown) saves the typed note
    without closing the sheet or applying "Mark done"; kv pending brings the sheet back."""
    from madhura.app import flush_data

    tid = active.repo.add_task("Write", 25, plan_date=active.today())
    sid = make_session(active, task="Write", task_id=tid)
    present_session_complete(active, sid, "completed", False)
    sheet = current_sheet(active)
    assert sheet.mark_done is not None and sheet.mark_done.isChecked()
    sheet.note.set_text("Numbers need a second look")
    flush_data(active)
    assert active.repo.get_session(sid).note == "Numbers need a second look"
    assert not active.repo.get_task(tid).done  # "Mark done" waits for the user's confirmation
    assert active.repo.kv_get(sc.PENDING_KEY) == sid
    assert current_sheet(active) is sheet  # still open (e.g. the logoff was cancelled)

    # The next launch's pending sheet: note filled in, "Mark done" checked for confirmation.
    again = SessionCompleteSheet(active, sid, "completed", pending=True)
    assert again.note.text() == "Numbers need a second look"
    assert again.mark_done is not None and again.mark_done.isChecked()
    again.deleteLater()

    # A cancelled logoff: the open sheet keeps working and commits everything when closed.
    sheet.note.set_text("Checked the numbers")
    sheet.done_button.click()
    pump()
    assert active.repo.get_session(sid).note == "Checked the numbers"
    assert active.repo.get_task(tid).done
    assert active.repo.kv_get(sc.PENDING_KEY) is None


def test_flush_points_pending_at_an_open_sheet(active: AppContext) -> None:
    from madhura.app import flush_data

    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    active.repo.kv_set(sc.PENDING_KEY, None)  # e.g. a later panel's "Keep going" cleared it
    flush_data(active)
    assert active.repo.kv_get(sc.PENDING_KEY) == sid
    assert active.repo.get_session(sid).note is None  # nothing typed → nothing written


# --------------------------------------------------------------------------- stale pending sheets


def _finish_now(ctx: AppContext, task: str = "Write") -> int:
    """A completed session that ended at the FakeClock's now."""
    now = ctx.clock.now().replace(microsecond=0)
    sid = ctx.repo.create_session(task, None, None, now, 25 * 60)
    ctx.repo.update_session(sid, focused_seconds=25 * 60, outcome="completed", ended_at=now)
    return sid


def test_late_pending_sheet_only_offers_done(shell: AppContext) -> None:  # noqa: F811
    shell.window.show_main()  # type: ignore[union-attr]
    pump()
    sid = make_session(shell)  # ended 09:45; the clock says 10:00
    shell.repo.kv_set(sc.PENDING_KEY, sid)
    sheet = show_pending_complete(shell)
    assert sheet is not None and sheet.pending and not sheet.offers_actions
    for w in (sheet.main_button, sheet.keep_button, sheet.break_row):
        assert not w.isVisibleTo(sheet)
    assert sheet.done_button.variant() == "primary" and sheet.primary_button() is sheet.done_button


def test_fresh_pending_sheet_still_offers_the_break(shell: AppContext) -> None:  # noqa: F811
    shell.window.show_main()  # type: ignore[union-attr]
    pump()
    sid = _finish_now(shell)
    shell.repo.kv_set(sc.PENDING_KEY, sid)
    sheet = show_pending_complete(shell)
    assert sheet is not None and sheet.offers_actions
    assert sheet.main_button.isVisibleTo(sheet) and sheet.main_button.text() == "Take a 5-min break"
    assert sheet.keep_button.isVisibleTo(sheet)


def test_pending_sheet_after_another_session_only_offers_done(shell: AppContext) -> None:  # noqa: F811
    shell.window.show_main()  # type: ignore[union-attr]
    pump()
    sid = _finish_now(shell, "First")
    _finish_now(shell, "Second")  # a later session ran (and ended) since
    sheet = SessionCompleteSheet(shell, sid, "completed", pending=True)
    assert not sheet.offers_actions
    sheet.deleteLater()


def test_pending_sheet_after_the_panels_break_only_offers_done(inactive: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    eng = inactive.engine
    eng.start("Write", 2)
    advance(inactive, 121, step=30)
    sid = eng_last_id(inactive)
    panel = panel_of(inactive)
    assert panel is not None and panel.session_id == sid
    panel.main_button.click()  # Take a 5-min break (from the panel)
    pump()
    assert str(eng.state) == "break"
    advance(inactive, 60, step=30)  # well inside the "still relevant" window
    sheet = SessionCompleteSheet(inactive, sid, "completed", pending=True)
    inactive.window.show_main()  # type: ignore[union-attr]
    inactive.window.show_sheet(sheet)  # type: ignore[union-attr]
    pump()
    assert not sheet.offers_actions
    assert not sheet.keep_button.isVisibleTo(sheet) and not sheet.main_button.isVisibleTo(sheet)
    assert sheet.done_button.variant() == "primary"
    sheet.close_sheet()
    pump()


# --------------------------------------------------------------------------- hidden-window outcomes


def test_too_short_finish_from_the_pill_notifies(inactive: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inactive.window, "is_main_visible", lambda: False)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(inactive.notifier, "notify", lambda t, b, *_a: sent.append((t, b)) or True)  # a tray
    present_session_complete(inactive, 99999, "abandoned", False)  # the row was deleted
    assert sent == [(sc.TOO_SHORT_TITLE, sc.TOO_SHORT_BODY)]
    assert not inactive.window.toasts.is_showing()  # type: ignore[union-attr]
    sid = make_session(inactive, outcome="abandoned", focused_s=300)
    present_session_complete(inactive, sid, "abandoned", False)  # explicit abandon: no noise
    assert len(sent) == 1
    assert not inactive.window.toasts.is_showing()  # type: ignore[union-attr]


def test_too_short_without_a_tray_waits_in_the_window(inactive: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    host = inactive.window.toasts  # type: ignore[union-attr]
    assert not inactive.window.isVisible()  # type: ignore[union-attr]
    present_session_complete(inactive, 99999, "abandoned", False)  # no tray: notify() can't show
    assert host.text() == sc.TOO_SHORT_TITLE and host.is_held()


def test_restored_notice_waits_for_the_window(shell: AppContext) -> None:  # noqa: F811
    """Autostart into the tray: the restored toast's countdown starts when the window shows."""
    win = shell.window
    assert not win.isVisible()  # type: ignore[union-attr]
    sid = make_session(shell, task="Write the report")
    present_session_complete(shell, sid, "completed", True)
    host = win.toasts  # type: ignore[union-attr]
    assert host.is_showing() and host.is_held()
    assert not host._timer.isActive()  # noqa: SLF001
    win.show_main()  # type: ignore[union-attr]
    pump()
    assert not host.is_held() and host._timer.isActive()  # noqa: SLF001
    assert host.text().startswith("Your session on ‘Write")


# --------------------------------------------------------------------------- panel layout


@pytest.mark.parametrize(("breaks", "minutes"), [(True, 5), (True, 10), (True, 15), (False, 5)])
def test_panel_fits_the_standard_width_with_regular_buttons(inactive: AppContext, breaks: bool, minutes: int) -> None:
    from madhura.ui.widgets.popup import PANEL_WIDTH, SHADOW_MARGIN

    inactive.settings.update(breaks_enabled=breaks, break_minutes=minutes)
    sid = make_session(inactive, task="Prepare slides for Thursday's sync with the whole design team")
    present_session_complete(inactive, sid, "completed", False)
    panel = panel_of(inactive)
    pump()
    assert panel.minimumSizeHint().width() <= PANEL_WIDTH + 2 * SHADOW_MARGIN
    assert panel.card_size().width() == PANEL_WIDTH
    assert panel.width() == PANEL_WIDTH + 2 * SHADOW_MARGIN
    assert panel.main_button.height() == 36 and panel.keep_button.height() == 36  # "md", like the other panels
    m, k = panel.main_button.geometry(), panel.keep_button.geometry()
    assert m.left() == 0 and k.right() == panel.content().width() - 1  # full content width, no indent
    assert panel.main_button.fontMetrics().horizontalAdvance(panel.main_button.text()) < m.width()
    assert panel.done_button.accessibleName() == "Done"
    assert panel.done_button.geometry().top() < panel.score_label.geometry().top()  # in the header


def test_primary_tooltip_uses_the_platform_modifier(active: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from madhura.ui import shortcuts_sheet

    monkeypatch.setattr(shortcuts_sheet, "sys", SimpleNamespace(platform="darwin"))
    sid = make_session(active)
    present_session_complete(active, sid, "completed", False)
    assert current_sheet(active).main_button.toolTip() == "Take a 5-min break · ⌘+Enter"
