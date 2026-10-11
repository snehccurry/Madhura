"""Focus page idle view [P1]: Plan card, Up next, Today, Parking lot, header, banners, layout."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from madhura.context import AppContext
from madhura.ui import theme
from madhura.ui.pages.focus.idle_view import (
    CARRYOVER_KEY,
    CUSTOM,
    GOAL_TOAST_KEY,
    TASK_ERROR,
    IdleView,
    PlanCard,
    TodayCard,
    UpNextCard,
)
from test_p1_support import TODAY, add_session, advance, focus, key, no_summary, page_of, pump, show, win_of  # noqa: F401


def _idle(ctx: AppContext) -> IdleView:
    return page_of(ctx).idle_view


def _card(ctx: AppContext) -> PlanCard:
    return _idle(ctx).plan_card


def _up(ctx: AppContext) -> UpNextCard:
    return _idle(ctx).up_next


# =========================================================================== Plan card


def test_lock_in_button_starts_with_duration_and_project(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    card = _card(focus)
    card.task_field.set_text("Write the intro")
    card.duration.set_value(25)
    card.project_field.set_text("Docs")
    qtbot.mouseClick(card.lock_in, Qt.MouseButton.LeftButton)
    e = focus.engine
    assert str(e.state) == "focus"
    assert e.task == "Write the intro" and e.planned_seconds == 25 * 60 and e.project == "Docs"
    assert card.task_field.text() == ""


def test_enter_in_task_field_starts(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.task_field.set_text("Outline")
    key(card.task_field.line_edit, Qt.Key.Key_Return)
    assert str(focus.engine.state) == "focus"
    assert focus.engine.planned_seconds == 60 * 60  # default_minutes


def test_enter_in_project_field_starts(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.task_field.set_text("Outline")
    card.project_field.set_text("Work")
    key(card.project_field.line_edit, Qt.Key.Key_Enter)
    assert str(focus.engine.state) == "focus" and focus.engine.project == "Work"


def test_ctrl_enter_starts_via_shell_shortcut(focus: AppContext) -> None:  # noqa: F811
    win = win_of(focus)
    _card(focus).task_field.set_text("Plan Q4")
    for sc in win.shortcuts():
        if sc.key().toString() in ("Ctrl+Return", "Ctrl+Enter"):
            sc.activated.emit()
            break
    assert str(focus.engine.state) == "focus" and focus.engine.task == "Plan Q4"


def test_empty_task_shows_error_and_typing_clears_it(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    card = _card(focus)
    assert card.start() is None
    assert str(focus.engine.state) == "idle"
    assert card.task_field.has_error() and card.task_field.error_text() == TASK_ERROR
    card.task_field.set_text("   ")
    assert card.start() is None and card.task_field.has_error()
    card.task_field.line_edit.setFocus()
    QTest.keyClicks(card.task_field.line_edit, "x")
    assert not card.task_field.has_error()


def test_plan_heading_follows_duration(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    assert card.heading.text() == "Plan your next hour"
    card.set_minutes(25)
    assert card.heading.text() == "Plan your next 25 minutes"
    card.set_minutes(90)
    assert card.heading.text() == "Plan your next 1.5 hours"
    card.set_minutes(120)
    assert card.heading.text() == "Plan your next 2 hours"
    assert card.duration.value() == CUSTOM and card.duration.label_for(CUSTOM) == "2h"


def test_duration_chips_follow_presets_setting(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    labels = [lab for _v, lab in card.duration.options()]
    assert labels == ["15m", "25m", "45m", "1h", "90m", "Custom"]
    assert card.duration.value() == 60
    focus.settings.update(duration_presets=[10, 30, 50])
    labels = [lab for _v, lab in card.duration.options()]
    assert labels[:3] == ["10m", "30m", "50m"] and labels[-1] in ("Custom", "1h")
    assert card.minutes() == 60  # selection kept (as Custom "1h")


def test_custom_duration_popover(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    card = _card(focus)
    rect = card.duration.chip_rect(CUSTOM)
    qtbot.mouseClick(card.duration, Qt.MouseButton.LeftButton, pos=rect.center())
    qtbot.waitUntil(lambda: card.custom_popover() is not None and card.custom_popover().is_open(), timeout=1000)
    stepper = card.custom_stepper()
    assert stepper is not None and (stepper.minimum(), stepper.maximum()) == (5, 240)
    assert card.duration.value() == CUSTOM and card.duration.label_for(CUSTOM) == "35m"
    assert card.heading.text() == "Plan your next 35 minutes"
    key(stepper, Qt.Key.Key_Up)
    assert card.minutes() == 40 and card.duration.label_for(CUSTOM) == "40m"
    assert card.heading.text() == "Plan your next 40 minutes"
    key(stepper, Qt.Key.Key_Return)  # Enter sets + closes
    assert card.custom_popover() is None
    assert card.duration.value() == CUSTOM and card.minutes() == 40
    card.task_field.set_text("Custom block")
    assert card.start() is not None
    assert focus.engine.planned_seconds == 40 * 60


def test_custom_value_equal_to_preset_selects_preset(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.open_custom_duration()
    card._apply_custom(45)  # noqa: SLF001 - what the stepper does on change
    card.close_popovers()
    pump()
    assert card.duration.value() == 45 and card.duration.label_for(CUSTOM) == "Custom"


def test_arrow_keys_onto_custom_do_not_open_popover(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.duration.set_value(90)
    card.duration.setFocus(Qt.FocusReason.TabFocusReason)
    key(card.duration, Qt.Key.Key_Right)
    pump()
    assert card.duration.value() == CUSTOM
    assert card.custom_popover() is None
    assert card.minutes() == 35
    key(card.duration, Qt.Key.Key_Space)  # explicit activation opens it
    pump()
    assert card.custom_popover() is not None
    card.close_popovers()


def test_ends_at_caption_time_format_and_checkins(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    assert card.helper.text() == "Ends at 11:00 · check-ins every 7 min"
    focus.settings.update(checkin_interval_minutes=10)
    assert card.helper.text() == "Ends at 11:00 · check-ins every 10 min"
    focus.settings.update(checkin_enabled=False)
    assert card.helper.text() == "Ends at 11:00 · check-ins off"
    focus.settings.update(time_format="12h")
    card.set_minutes(25)
    assert card.helper.text() == "Ends at 10:25 AM · check-ins off"


def test_completers_and_project_dot(focus: AppContext) -> None:  # noqa: F811
    add_session(focus, "Write report", datetime(2026, 10, 13, 9, 0), 30, project="Work")
    add_session(focus, "Study Rust", datetime(2026, 10, 13, 11, 0), 30, project="Learning")
    card = _card(focus)
    card.refresh_completers()
    titles = card._task_model.stringList()  # noqa: SLF001
    assert titles[:2] == ["Study Rust", "Write report"]
    assert set(card._project_model.stringList()) == {"Work", "Learning"}  # noqa: SLF001
    assert card.task_field.line_edit.completer() is not None
    assert card.project_field.line_edit.completer() is not None
    card.project_field.set_text("Work")
    dot = card.project_field.frame._dot  # noqa: SLF001
    assert dot is not None and dot.name().upper() == theme.project_color("Work").upper()
    # Picking a remembered title fills its last project.
    card.project_field.set_text("")
    card._on_title_completed("Write report")  # noqa: SLF001 - completer.activated[str]
    assert card.project_field.text() == "Work"


def test_keyboard_pick_from_the_completer_keeps_the_remembered_project(focus: AppContext) -> None:  # noqa: F811
    """Typing "deep", ↓, Enter: the session starts with the past task's project, as a mouse pick
    would (QLineEdit starts the session before the completer's ``activated`` fires)."""
    add_session(focus, "Deep work on parser", datetime(2026, 10, 13, 9, 0), 30, project="Compiler")
    card = _card(focus)
    card.refresh_completers()
    edit = card.task_field.line_edit
    win_of(focus).activateWindow()
    edit.setFocus()
    QTest.keyClicks(edit, "deep")
    pump()
    popup = edit.completer().popup()
    assert popup.isVisible()
    QTest.keyClick(popup, Qt.Key.Key_Down)  # keys go to the open popup, as on a real desktop
    QTest.keyClick(popup, Qt.Key.Key_Return)
    pump()
    e = focus.engine
    assert str(e.state) == "focus" and e.task == "Deep work on parser"
    assert e.project == "Compiler"
    assert card.project_field.text() == ""  # no stale project left behind for the next block


def test_cleared_project_after_a_completer_pick_stays_cleared(focus: AppContext) -> None:  # noqa: F811
    add_session(focus, "Deep work on parser", datetime(2026, 10, 13, 9, 0), 30, project="Compiler")
    card = _card(focus)
    card.refresh_completers()
    card._on_title_highlighted("Deep work on parser")  # noqa: SLF001 - mouse hover, then click
    card.task_field.set_text("Deep work on parser")
    card._on_title_completed("Deep work on parser")  # noqa: SLF001
    assert card.project_field.text() == "Compiler"
    card.project_field.set_text("")  # the user doesn't want a project this time
    card.start()
    assert focus.engine.project is None


def test_project_completer_suggests_projects(focus: AppContext) -> None:  # noqa: F811
    add_session(focus, "Write report", datetime(2026, 10, 13, 9, 0), 30, project="Writing")
    card = _card(focus)
    card.refresh_completers()
    completer = card.project_field.line_edit.completer()
    completer.setCompletionPrefix("wri")
    assert completer.currentCompletion() == "Writing"


def test_break_picker_starts_labelled_break(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.open_break_picker()
    pump()
    picker = card.break_picker()
    assert picker is not None
    labels, minutes, start = picker
    assert [lab for _v, lab in labels.options()] == ["Break", "Lunch", "Meeting", "Admin"]
    assert [v for v, _lab in minutes.options()] == [15, 30, 45, 60]
    labels.setFocus()
    key(labels, Qt.Key.Key_Right)  # → Lunch (default 45)
    assert labels.value() == "Lunch" and minutes.value() == 45 and start.text() == "Start lunch"
    minutes.set_value(30)
    start.click()
    e = focus.engine
    assert str(e.state) == "break" and e.break_label == "Lunch" and e.break_total_seconds == 30 * 60
    assert card.break_picker() is None


def test_title_match_links_up_next_task(focus: AppContext) -> None:  # noqa: F811
    tid = focus.repo.add_task("Write intro", 25, plan_date=TODAY)
    card = _card(focus)
    card.task_field.set_text("write intro")
    card.start()
    assert focus.engine.planned_task_id == tid


def test_prefill_after_session_from_first_unfinished(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    done = repo.add_task("Already done", 25, plan_date=TODAY)
    repo.set_task_done(done, True)
    first = repo.add_task("Prepare slides", 45, "Work", plan_date=TODAY)
    repo.add_task("Reply to Priya", 15, plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    card = _card(focus)
    card.task_field.set_text("Warm-up")
    card.start()
    advance(focus, 120)
    focus.engine.finish()
    pump()
    assert page_of(focus).is_idle_shown()
    assert card.task_field.text() == "Prepare slides"
    assert card.task_field.line_edit.selectedText() == "Prepare slides"
    assert card.minutes() == 45 and card.project() == "Work" and card.planned_task_id() == first
    card.start()
    assert focus.engine.planned_task_id == first and focus.engine.planned_seconds == 45 * 60


def test_prefill_empty_when_no_up_next(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.task_field.set_text("Session")
    card.project_field.set_text("Work")
    card.start()
    advance(focus, 120)
    focus.engine.finish()
    pump()
    assert card.task_field.text() == "" and card.project() is None and card.planned_task_id() is None


def test_prefill_after_break_ends(focus: AppContext) -> None:  # noqa: F811
    focus.repo.add_task("Next thing", 25, plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    focus.engine.start_break(1)
    advance(focus, 61, step=30)
    pump()
    assert _card(focus).task_field.text() == "Next thing"


def test_untouched_prefill_follows_up_next(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("First", 25, plan_date=TODAY)
    repo.add_task("Second", 45, plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    card, up = _card(focus), _up(focus)
    card.prefill(up.first_unfinished())
    assert card.task_field.text() == "First"
    up.toggle_done(a)
    assert card.task_field.text() == "Second" and card.minutes() == 45
    card.task_field.set_text("My own text")
    up.toggle_done(a)  # un-done: "First" is first again, but the user's text stays
    assert card.task_field.text() == "My own text"


# =========================================================================== Up next


def test_add_row_adds_task_and_keeps_focus(focus: AppContext) -> None:  # noqa: F811
    up = _up(focus)
    assert up.empty_label.isVisible()
    up.add_field.line_edit.setFocus()
    up.add_field.set_text("Write tests")
    key(up.add_field.line_edit, Qt.Key.Key_Return)
    tasks = focus.repo.tasks_for_date(TODAY)
    assert [(t.title, t.minutes) for t in tasks] == [("Write tests", 25)]
    assert up.add_field.text() == ""
    assert [t.title for t in up.tasks()] == ["Write tests"]
    assert not up.empty_label.isVisible() and up.count_label.text() == "1"
    key(up.add_field.line_edit, Qt.Key.Key_Return)  # blank → ignored
    assert len(focus.repo.tasks_for_date(TODAY)) == 1


def test_add_badge_cycles_presets(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    up = _up(focus)
    assert up.add_minutes() == 25 and up.add_minutes_badge.text() == "25m"
    seen = []
    for _ in range(5):
        qtbot.mouseClick(up.add_minutes_badge, Qt.MouseButton.LeftButton)
        seen.append(up.add_minutes())
    assert seen == [45, 60, 90, 15, 25]
    up.cycle_add_minutes()
    up.add_task("Long one")
    assert focus.repo.tasks_for_date(TODAY)[0].minutes == 45


def test_done_toggle_click_and_space(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    repo = focus.repo
    a = repo.add_task("A", plan_date=TODAY)
    b = repo.add_task("B", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    row = up.row_for(a)
    qtbot.mouseClick(row.check, Qt.MouseButton.LeftButton)
    assert repo.get_task(a).done
    assert [t.title for t in up.tasks()] == ["B", "A"]  # done sink to the bottom
    assert up.count_label.text() == "1"
    up.list.setFocus()
    up.list.select_id(b)
    key(up.list, Qt.Key.Key_Space)
    assert repo.get_task(b).done
    assert up.count_label.isHidden()


def test_start_button_starts_planned_task(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    tid = focus.repo.add_task("Prepare slides", 45, "Work", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    row = _up(focus).row_for(tid)
    row.start_button.setVisible(True)
    qtbot.mouseClick(row.start_button, Qt.MouseButton.LeftButton)
    e = focus.engine
    assert str(e.state) == "focus" and e.task == "Prepare slides" and e.planned_seconds == 45 * 60
    assert e.project == "Work" and e.planned_task_id == tid


def test_start_ignored_for_done_or_active(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("A", plan_date=TODAY)
    b = repo.add_task("B", plan_date=TODAY)
    repo.set_task_done(a, True)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    assert up.start_task(a) is None
    assert up.start_task(b) is not None
    assert up.start_task(b) is None  # already running


def test_delete_with_undo_and_commit(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("Keep", plan_date=TODAY)
    b = repo.add_task("Remove me", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up, win = _up(focus), win_of(focus)
    up.delete_task(b)
    assert [t.id for t in up.tasks()] == [a]
    assert repo.get_task(b) is not None  # not deleted until the toast goes
    assert win.toasts.text().startswith("Deleted") and win.toasts.action_text() == "Undo"
    win.toasts.trigger_action()
    assert [t.id for t in up.tasks()] == [a, b] and repo.get_task(b) is not None
    up.delete_task(b)
    win.flush_toasts()  # quit / expiry commits
    assert repo.get_task(b) is None
    assert [t.id for t in up.tasks()] == [a]


def test_delete_key_and_replacement_toast_commits(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("One", plan_date=TODAY)
    b = repo.add_task("Two", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up, win = _up(focus), win_of(focus)
    up.list.setFocus()
    up.list.select_id(a)
    key(up.list, Qt.Key.Key_Delete)
    assert repo.get_task(a) is not None and up.list.selected_id() == b
    win.show_toast("Something else")  # replaces the undo toast → commit
    assert repo.get_task(a) is None


def test_held_delete_key_deletes_one_task(focus: AppContext) -> None:  # noqa: F811
    """Auto-repeat of a held Delete / Backspace is ignored (each repeat would commit the
    previous delete by replacing its undo toast)."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    repo = focus.repo
    a = repo.add_task("One", plan_date=TODAY)
    b = repo.add_task("Two", plan_date=TODAY)
    c = repo.add_task("Three", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    up.list.setFocus()
    up.list.select_id(a)
    key(up.list, Qt.Key.Key_Delete)
    assert up.list.selected_id() == b
    for k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        QApplication.sendEvent(up.list, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, "", True))
    assert [t.id for t in up.tasks()] == [b, c] and up.list.selected_id() == b
    assert repo.get_task(a) is not None  # still undoable


def test_keyboard_selection_is_described_for_assistive_tech(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    repo.add_task("Reply to reviewer", 25, plan_date=TODAY)
    b = repo.add_task("Write the report", 45, plan_date=TODAY)
    c = repo.add_task("Read", 15, plan_date=TODAY)
    repo.set_task_done(c, True)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    assert up.list.accessibleName() == "Up next tasks" and up.list.accessibleDescription() == ""
    up.list.setFocus(Qt.FocusReason.TabFocusReason)
    pump()
    assert up.list.accessibleDescription() == "Reply to reviewer, 25 min, 1 of 3"
    key(up.list, Qt.Key.Key_Down)
    assert up.list.selected_id() == b
    assert up.list.accessibleDescription() == "Write the report, 45 min, 2 of 3"
    key(up.list, Qt.Key.Key_End)
    assert up.list.accessibleDescription() == "Read, 15 min, done, 3 of 3"
    win_of(focus).pages["focus"].idle_view.plan_card.task_field.line_edit.setFocus()
    pump()
    assert up.list.accessibleDescription() == ""


def test_reorder_with_alt_arrows(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    ids = [repo.add_task(t, plan_date=TODAY) for t in ("A", "B", "C")]
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    up.list.setFocus()
    up.list.select_id(ids[2])
    key(up.list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert [t.title for t in repo.tasks_for_date(TODAY)] == ["A", "C", "B"]
    key(up.list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert [t.title for t in repo.tasks_for_date(TODAY)] == ["C", "A", "B"]
    assert up.list.selected_id() == ids[2]
    key(up.list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)  # already first
    key(up.list, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert [t.title for t in up.tasks()] == ["A", "C", "B"]


def test_move_to_tomorrow_and_undo(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("A", plan_date=TODAY)
    b = repo.add_task("B", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up, win = _up(focus), win_of(focus)
    target = up.move_to_tomorrow(a)
    assert target == date(2026, 10, 15)
    assert repo.get_task(a).plan_date == target and [t.id for t in up.tasks()] == [b]
    assert win.toasts.text() == "Moved to tomorrow"
    win.toasts.trigger_action()
    assert [t.id for t in repo.tasks_for_date(TODAY)] == [a, b]


def test_move_to_tomorrow_skips_non_work_days(focus: AppContext) -> None:  # noqa: F811
    focus.clock.set(datetime(2026, 10, 16, 10, 0))  # Friday
    focus.events.day_changed.emit()
    tid = focus.repo.add_task("Weekend-proof", plan_date=date(2026, 10, 16))
    focus.events.data_changed.emit("tasks")
    assert _up(focus).move_to_tomorrow(tid) == date(2026, 10, 19)  # Monday (work_days Mon–Fri)
    assert win_of(focus).toasts.text() == "Moved to Mon 19 Oct"


def test_keyboard_navigation_enter_starts_and_down_reaches_add_row(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("A", plan_date=TODAY)
    b = repo.add_task("B", 15, plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    up.list.setFocus(Qt.FocusReason.TabFocusReason)
    pump()
    assert up.list.selected_id() == a and up.row_for(a).is_selected()
    key(up.list, Qt.Key.Key_Down)
    assert up.list.selected_id() == b
    key(up.list, Qt.Key.Key_Down)  # past the last row → add field
    assert up.add_field.line_edit.hasFocus()
    key(up.add_field.line_edit, Qt.Key.Key_Up)  # back to the last row
    assert up.list.hasFocus() and up.list.selected_id() == b
    key(up.list, Qt.Key.Key_Up)
    assert up.list.selected_id() == a
    key(up.list, Qt.Key.Key_Down)
    key(up.list, Qt.Key.Key_Return)
    assert str(focus.engine.state) == "focus" and focus.engine.planned_task_id == b


def test_inline_edit_commit_and_cancel(focus: AppContext) -> None:  # noqa: F811
    tid = focus.repo.add_task("Old title", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    up.begin_edit(tid)
    row = up.row_for(tid)
    assert row.is_editing()
    editor = row.editor()
    editor.set_text("New title")
    key(editor.line_edit, Qt.Key.Key_Return)
    pump()
    assert focus.repo.get_task(tid).title == "New title"
    assert up.row_for(tid).title.full_text() == "New title"
    up.list.setFocus()
    up.list.select_id(tid)
    key(up.list, Qt.Key.Key_F2)
    row = up.row_for(tid)
    row.editor().set_text("Discarded")
    key(row.editor().line_edit, Qt.Key.Key_Escape)
    pump()
    assert focus.repo.get_task(tid).title == "New title" and not up.row_for(tid).is_editing()


def test_row_menu_actions(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    a = repo.add_task("Open", plan_date=TODAY)
    b = repo.add_task("Finished", plan_date=TODAY)
    repo.set_task_done(b, True)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    menu = up.open_menu(a)
    texts = [act.text() for act in menu.actions() if not act.isSeparator()]
    assert texts == ["Edit", "Move to tomorrow", "Delete"]
    menu.close()
    menu = up.open_menu(b)
    assert [act.text() for act in menu.actions() if not act.isSeparator()] == ["Edit", "Delete"]
    next(act for act in menu.actions() if act.text() == "Delete").trigger()
    assert b in up.pending_deletes()
    menu.close()


def test_task_minutes_badge_cycles(focus: AppContext) -> None:  # noqa: F811
    tid = focus.repo.add_task("A", 25, plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    up = _up(focus)
    up.row_for(tid).minutes_badge.clicked.emit()
    assert focus.repo.get_task(tid).minutes == 45
    assert up.row_for(tid).minutes_badge.text() == "45m"


def test_up_next_uses_logical_today(focus: AppContext) -> None:  # noqa: F811
    focus.clock.set(datetime(2026, 10, 15, 2, 30))  # still Oct 14 with day_start_hour 4
    focus.events.day_changed.emit()
    assert focus.today() == TODAY
    up = _up(focus)
    up.add_task("Late night")
    assert focus.repo.tasks_for_date(TODAY)[0].title == "Late night"


# =========================================================================== carry-over banner


def test_carryover_banner_move_to_today(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    y = TODAY - timedelta(days=1)
    a = repo.add_task("Old A", plan_date=y)
    b = repo.add_task("Old B", plan_date=y - timedelta(days=3))
    done = repo.add_task("Old done", plan_date=y)
    repo.set_task_done(done, True)
    focus.events.data_changed.emit("tasks")
    idle = page_of(focus).idle_view
    banner = idle.carryover_banner
    assert banner.isVisible() and banner.text() == "2 unfinished from earlier"
    assert [btn.text() for btn in banner.action_buttons()] == ["Move to today", "Dismiss"]
    banner.action_buttons()[0].click()
    assert not banner.isVisible()
    assert {t.id for t in repo.tasks_for_date(TODAY)} == {a, b}
    assert {t.id for t in _up(focus).tasks()} == {a, b}


def test_carryover_dismiss_is_per_day(focus: AppContext) -> None:  # noqa: F811
    repo = focus.repo
    repo.add_task("Old", plan_date=TODAY - timedelta(days=1))
    focus.events.data_changed.emit("tasks")
    idle = page_of(focus).idle_view
    idle.carryover_banner.action_buttons()[1].click()
    assert not idle.carryover_banner.isVisible()
    assert repo.kv_get(CARRYOVER_KEY) == "2026-10-14"
    idle.refresh()
    assert not idle.carryover_banner.isVisible()
    focus.clock.set(datetime(2026, 10, 15, 9, 0))
    focus.events.day_changed.emit()
    assert idle.carryover_banner.isVisible()


# =========================================================================== Today card


def test_today_card_rows_scores_and_total(focus: AppContext) -> None:  # noqa: F811
    add_session(focus, "Draft", datetime(2026, 10, 14, 8, 0), 45, checkins=("focused",) * 6 + ("drifted",), project="Work")
    add_session(focus, "Review", datetime(2026, 10, 14, 9, 0), 25)
    add_session(focus, "Roadmap", datetime(2026, 10, 14, 9, 30), 38, "ended_early", planned_min=60,
                checkins=("focused", "focused", "drifted", "missed"))
    add_session(focus, "Discarded", datetime(2026, 10, 14, 9, 40), 5, "abandoned")
    add_session(focus, "Before day start", datetime(2026, 10, 14, 3, 30), 30)  # logical Oct 13
    add_session(focus, "Yesterday", datetime(2026, 10, 13, 15, 0), 30)
    focus.events.data_changed.emit("sessions")
    card: TodayCard = page_of(focus).idle_view.today_card
    rows = card.rows()
    assert [r.session.task for r in rows] == ["Draft", "Review", "Roadmap"]
    assert [r.caption.full_text() for r in rows] == [
        "45m · 86% focused",
        "25m",
        "38m · 67% focused · ended early",
    ]
    assert [r.time_label.text() for r in rows] == ["08:00", "09:00", "09:30"]
    assert card.total_label.text() == "1h 48m"
    assert not card.empty_label.isVisible()
    assert rows[0].rail_color().name().upper() == theme.project_color("Work").upper()


def test_today_card_empty_and_active_excluded(focus: AppContext) -> None:  # noqa: F811
    card = page_of(focus).idle_view.today_card
    assert card.empty_label.isVisible() and card.empty_label.text() == "No sessions yet today."
    assert card.total_label.text() == ""
    focus.repo.create_session("Running", None, None, datetime(2026, 10, 14, 9, 0), 1500)  # 'active'
    card.refresh()
    assert card.rows() == []


def test_today_card_12h_times(focus: AppContext) -> None:  # noqa: F811
    focus.settings.update(time_format="12h")
    add_session(focus, "Afternoon", datetime(2026, 10, 14, 13, 5), 25)
    focus.events.data_changed.emit("sessions")
    row = page_of(focus).idle_view.today_card.rows()[0]
    assert row.time_label.text() == "1:05 PM"
    assert row.time_label.width() >= row.time_label.fontMetrics().horizontalAdvance("12:59 PM")


# =========================================================================== Parking lot


def test_parking_lot_hidden_when_empty_and_clear_undo(focus: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    idle = page_of(focus).idle_view
    lot = idle.parking_lot
    assert not lot.isVisible()
    cid = focus.repo.add_capture("Book the dentist")
    focus.events.data_changed.emit("captures")
    assert lot.isVisible() and lot.count_label.text() == "1"
    row = lot.rows()[0]
    assert row.title.full_text() == "Book the dentist"
    qtbot.mouseClick(row.check, Qt.MouseButton.LeftButton)
    assert focus.repo.open_captures() == [] and not lot.isVisible()
    win = win_of(focus)
    assert win.toasts.action_text() == "Undo"
    win.toasts.trigger_action()
    assert [c.id for c in focus.repo.open_captures()] == [cid] and lot.isVisible()


def test_parking_lot_to_up_next_and_undo(focus: AppContext) -> None:  # noqa: F811
    cid = focus.repo.add_capture("Look into flaky CI")
    focus.events.data_changed.emit("captures")
    lot = page_of(focus).idle_view.parking_lot
    row = lot.rows()[0]
    row.to_next.click()
    tasks = focus.repo.tasks_for_date(TODAY)
    assert [(t.title, t.minutes) for t in tasks] == [("Look into flaky CI", 25)]
    assert focus.repo.open_captures() == []
    assert [t.title for t in _up(focus).tasks()] == ["Look into flaky CI"]
    win_of(focus).toasts.trigger_action()
    assert focus.repo.tasks_for_date(TODAY) == [] and [c.id for c in focus.repo.open_captures()] == [cid]


def test_rows_are_updated_in_place_not_rebuilt(focus: AppContext) -> None:  # noqa: F811
    """A done toggle / one cleared capture touches only what changed (no full rebuild)."""
    repo = focus.repo
    ids = [repo.add_task(f"Task {i}", 25, plan_date=TODAY) for i in range(5)]
    caps = [repo.add_capture(f"Thought {i}") for i in range(6)]
    focus.events.data_changed.emit("tasks")
    focus.events.data_changed.emit("captures")
    pump()
    up = _up(focus)
    before = {r.task.id: r for r in up.list.rows}
    up.toggle_done(ids[1])
    pump()
    after = {r.task.id: r for r in up.list.rows}
    assert all(after[i] is before[i] for i in ids)  # the same widgets, updated
    assert [r.task.id for r in up.list.rows] == [ids[0], ids[2], ids[3], ids[4], ids[1]]  # done sank
    done = after[ids[1]]
    assert done.check.isChecked() and not done.start_button.isEnabled()
    assert done.title.property("tone") == "muted"
    assert up.list._layout.count() == 5  # noqa: SLF001 - laid out once each, nothing left over
    # Edits show up in place too.
    repo.update_task(ids[0], title="Renamed", minutes=45)
    focus.events.data_changed.emit("tasks")
    pump()
    row = up.row_for(ids[0])
    assert row is before[ids[0]]
    assert row.title.full_text() == "Renamed" and row.minutes_badge.text() == "45m"
    assert row.start_button.accessibleName() == "Start ‘Renamed’"
    up.toggle_done(ids[1])  # back to unfinished
    pump()
    assert not up.row_for(ids[1]).check.isChecked() and up.row_for(ids[1]).start_button.isEnabled()
    assert up.row_for(ids[1]).title.property("tone") in ("", None)
    # Parking lot: clearing one capture keeps every other row.
    lot = page_of(focus).idle_view.parking_lot
    rows = {r.capture.id: r for r in lot.rows()}
    lot.clear_capture(caps[2])
    pump()
    left = {r.capture.id: r for r in lot.rows()}
    assert set(left) == set(caps) - {caps[2]}
    assert all(left[c] is rows[c] for c in left)
    assert lot.count_label.text() == "5"


def test_parking_lot_action_shows_on_hover(focus: AppContext) -> None:  # noqa: F811
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent

    focus.repo.add_capture("Idea")
    focus.events.data_changed.emit("captures")
    row = page_of(focus).idle_view.parking_lot.rows()[0]
    assert row.to_next.isHidden()
    QApplication.sendEvent(row, QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert row.to_next.isVisible()
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    assert row.to_next.isHidden()


# =========================================================================== header: goal ring, streak, toast


def test_goal_ring_label_and_one_time_toast(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    header = idle.header
    add_session(focus, "A", datetime(2026, 10, 14, 8, 0), 80)
    focus.events.data_changed.emit("sessions")
    assert header.goal_text() == "1h 20m / 2h"
    assert not header.goal_reached() and abs(header.goal_ring.progress() - 80 / 120) < 1e-6
    assert header.goal_ring._color.name().upper() == theme.palette().accent.upper()  # noqa: SLF001
    win = win_of(focus)
    shown: list[str] = []
    original = win.show_toast

    def record(text, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        shown.append(text)
        return original(text, *a, **k)

    win.show_toast = record  # type: ignore[method-assign]
    add_session(focus, "B", datetime(2026, 10, 14, 9, 30), 45)
    focus.events.data_changed.emit("sessions")
    assert header.goal_reached() and header.goal_text() == "2h 5m / 2h"
    assert header.goal_ring._color.name().upper() == theme.palette().success.upper()  # noqa: SLF001
    assert shown == ["Daily goal reached — 2h focused"]
    assert focus.repo.kv_get(GOAL_TOAST_KEY) == "2026-10-14"
    add_session(focus, "C", datetime(2026, 10, 14, 9, 50), 10)
    focus.events.data_changed.emit("sessions")
    idle.refresh()
    assert shown == ["Daily goal reached — 2h focused"]  # once per day


def test_goal_toast_waits_until_visible(focus: AppContext) -> None:  # noqa: F811
    win = win_of(focus)
    win.hide()
    add_session(focus, "Long", datetime(2026, 10, 14, 8, 0), 130)
    focus.events.data_changed.emit("sessions")
    assert focus.repo.kv_get(GOAL_TOAST_KEY) is None
    assert "header" in page_of(focus).idle_view.dirty_sections()
    show(focus)
    pump()
    assert focus.repo.kv_get(GOAL_TOAST_KEY) == "2026-10-14"


def test_streak_chip_hidden_at_zero_and_counts_goal_days(focus: AppContext) -> None:  # noqa: F811
    header = page_of(focus).idle_view.header
    assert header.streak.isHidden()
    # Goal (2h) met Mon 12 + Tue 13; Sat/Sun 10-11 are non-work days (neutral); Fri 9 met.
    for d in (9, 12, 13):
        add_session(focus, "Deep work", datetime(2026, 10, d, 9, 0), 125)
    focus.events.data_changed.emit("sessions")
    assert header.streak.isVisible() and header.streak.count() == 3
    assert "3-day goal streak" in header.streak.toolTip()


def test_streak_broken_by_missed_work_day(focus: AppContext) -> None:  # noqa: F811
    for d in (12, 13):
        add_session(focus, "Deep work", datetime(2026, 10, d, 9, 0), 125)
    add_session(focus, "Deep work", datetime(2026, 10, 8, 9, 0), 125)  # Thu 8; Fri 9 missed
    focus.events.data_changed.emit("sessions")
    header = page_of(focus).idle_view.header
    assert header.streak.count() == 2
    assert "best" not in header.streak.toolTip()


def test_greeting_and_date(focus: AppContext) -> None:  # noqa: F811
    header = page_of(focus).idle_view.header
    assert header.greeting.text() == "Good morning"
    assert header.date_label.text() == "Wednesday, 14 October"


# =========================================================================== layout / refresh / misc


def test_two_columns_when_wide_and_single_column_when_narrow(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    assert idle.width() >= 900 and idle.is_two_columns()
    left, right = idle.left_column.geometry(), idle.right_column.geometry()
    assert right.width() == 320 and right.left() >= left.right() + 20 - 1
    assert left.width() >= 480
    assert idle.column.width() <= 1040
    show(focus, 900, 700)
    assert not idle.is_two_columns()
    left, right = idle.left_column.geometry(), idle.right_column.geometry()
    assert right.top() > left.bottom() and abs(right.left() - left.left()) <= 1
    today = idle.today_card.mapTo(idle.column, QPoint(0, 0))
    upnext = idle.up_next.mapTo(idle.column, QPoint(0, 0))
    assert today.y() > upnext.y()
    show(focus, 1120, 760)
    assert idle.is_two_columns()


def test_side_by_side_card_titles_share_a_baseline(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    show(focus, 1120, 760)
    assert idle.is_two_columns()
    plan = idle.plan_card.heading
    today = idle.today_card._title  # noqa: SLF001
    assert plan.mapTo(idle.column, QPoint(0, 0)).y() == today.mapTo(idle.column, QPoint(0, 0)).y()


def test_plan_card_controls_reflow(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    show(focus, 1120, 760)
    assert not card.is_single_row()  # 320 px right column leaves too little room
    lock = card.lock_in.geometry()
    assert lock.right() <= card.width() - 24 + 1  # inside the padding
    show(focus, 1500, 900)
    assert card.lock_in.geometry().right() <= card.width() - 24 + 1


def test_refresh_deferred_while_hidden(focus: AppContext) -> None:  # noqa: F811
    win = win_of(focus)
    idle = page_of(focus).idle_view
    win.show_page("habits")
    pump()
    focus.repo.add_task("Added elsewhere", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    assert "upnext" in idle.dirty_sections()
    assert idle.up_next.tasks() == []
    win.show_page("focus")
    pump()
    assert [t.title for t in idle.up_next.tasks()] == ["Added elsewhere"]
    assert not idle.dirty_sections()


def test_day_change_refreshes_everything(focus: AppContext) -> None:  # noqa: F811
    idle = page_of(focus).idle_view
    focus.repo.add_task("Tomorrow's", plan_date=date(2026, 10, 15))
    focus.clock.set(datetime(2026, 10, 15, 9, 0))
    focus.events.day_changed.emit()
    assert idle.header.date_label.text() == "Thursday, 15 October"
    assert [t.title for t in idle.up_next.tasks()] == ["Tomorrow's"]


def test_wrap_up_link_opens_sheet(focus: AppContext) -> None:  # noqa: F811
    from madhura.ui.overlays.wrap_up import WrapUpSheet

    idle = page_of(focus).idle_view
    idle.wrap_up_link.click()
    assert isinstance(win_of(focus).sheet_host.current(), WrapUpSheet)


def test_weekly_recap_embedding(qapp, data_dir, fake_clock, monkeypatch, no_summary) -> None:  # noqa: ANN001, F811
    from madhura.ui.widgets import recap as recap_mod
    from test_p1_support import dispose_shell, make_shell

    def should_show(self) -> bool:  # noqa: ANN001
        return True

    monkeypatch.setattr(recap_mod.WeeklyRecapCard, "should_show", should_show)
    ctx = make_shell(qapp, data_dir, fake_clock)
    try:
        show(ctx)
        idle = page_of(ctx).idle_view
        idle.refresh()
        assert idle.recap is not None and idle.recap.isVisible()
        idle.refresh()
        assert idle.recap.isVisible()
        idle.recap.dismissed.emit()
        assert not idle.recap.isVisible()
        idle.refresh()
        assert not idle.recap.isVisible()
    finally:
        dispose_shell(ctx, qapp)


def test_weekly_recap_returns_next_week_after_a_dismiss(focus: AppContext) -> None:  # noqa: F811
    """Tray-resident app running across weeks: a dismiss hides this week's recap only."""
    from madhura.ui.widgets.recap import SEEN_KEY

    add_session(focus, "Last week", datetime(2026, 10, 6, 9, 0), 120)  # week of Mon 5 Oct
    idle = page_of(focus).idle_view
    idle.refresh()
    pump()
    assert idle.recap is not None and idle.recap.isVisible()
    idle.recap.dismiss()
    pump()
    assert not idle.recap.isVisible()
    # Same week, next day: stays dismissed.
    focus.clock.advance(24 * 3600)  # type: ignore[attr-defined]
    focus.events.day_changed.emit()
    pump()
    assert not idle.recap.isVisible()
    # Next Monday, with focus in the (now) previous week: the new recap shows.
    add_session(focus, "This week", datetime(2026, 10, 14, 9, 0), 90)
    focus.clock.advance(4 * 24 * 3600)  # type: ignore[attr-defined] - Mon 19 Oct
    focus.events.day_changed.emit()
    pump()
    assert idle.recap.isVisible()
    assert focus.repo.kv_get(SEEN_KEY) == "2026-W43"
    assert "1h 30m" in idle.recap.text()
    # Another day in that week with the card still up: it stays.
    focus.clock.advance(24 * 3600)  # type: ignore[attr-defined]
    focus.events.day_changed.emit()
    pump()
    assert idle.recap.isVisible()


def test_idle_view_accessibility_names(focus: AppContext) -> None:  # noqa: F811
    tid = focus.repo.add_task("Write", plan_date=TODAY)
    focus.events.data_changed.emit("tasks")
    card = _card(focus)
    assert card.task_field.line_edit.accessibleName() == "Task"
    assert card.project_field.line_edit.accessibleName() == "Project (optional)"
    assert card.duration.accessibleName() == "Session length"
    row = _up(focus).row_for(tid)
    assert row.check.accessibleName() == "Mark ‘Write’ done"
    assert row.start_button.accessibleName() == "Start ‘Write’"
    assert row.more_button.accessibleName() == "More actions for ‘Write’"
    header = page_of(focus).idle_view.header
    assert header.goal_ring.accessibleName() == "Daily goal"
    assert "focused today" in header.goal_ring.accessibleDescription()


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_theme_switch_updates_tokens(focus: AppContext, theme_name: str) -> None:  # noqa: F811
    card = _card(focus)
    card.project_field.set_text("Work")
    focus.settings.update(theme=theme_name)
    pump()
    dot = card.project_field.frame._dot  # noqa: SLF001
    assert dot.name().upper() == theme.project_color("Work").upper()
    header = page_of(focus).idle_view.header
    assert header.goal_ring._color.name().upper() == theme.palette().accent.upper()  # noqa: SLF001


def test_focus_task_input_focuses_and_selects(focus: AppContext) -> None:  # noqa: F811
    card = _card(focus)
    card.task_field.set_text("Something")
    page_of(focus).focus_task_input()
    pump()
    assert card.task_field.line_edit.hasFocus()
    assert card.task_field.line_edit.selectedText() == "Something"


def test_ctrl_n_from_other_page_focuses_task(focus: AppContext) -> None:  # noqa: F811
    win = win_of(focus)
    win.show_page("insights")
    for sc in win.shortcuts():
        if sc.key().toString() == "Ctrl+N":
            sc.activated.emit()
    pump()
    assert win.current_page() == "focus"
    assert _card(focus).task_field.line_edit.hasFocus()
