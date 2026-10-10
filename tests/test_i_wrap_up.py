"""WrapUpSheet [I]: summary, unfinished tasks, captures, day note, tomorrow's first block, shutdown."""

# ruff: noqa: F811 - pytest fixtures are imported from test_i_support and used as parameters
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from madhura.context import AppContext
from madhura.ui.overlays.wrap_up import WrapUpSheet, next_day_word, open_wrap_up
from test_i_support import TODAY, add_session, ictx, no_summary, pump, show, win_of  # noqa: F401

THU = date(2026, 10, 15)


@pytest.fixture
def wctx(ictx: AppContext) -> Iterator[AppContext]:
    """Shell on the Focus page with a little of everything for today."""
    repo = ictx.repo
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 50)
    add_session(ictx, datetime(2026, 10, 14, 9, 0), 10, "ended_early")
    add_session(ictx, datetime(2026, 10, 14, 7, 0), 30, "abandoned")
    repo.add_task("Old one", 15, plan_date=TODAY - timedelta(days=2))
    repo.add_task("Slides", 45, "Work", plan_date=TODAY)
    done = repo.add_task("Done already", 25, plan_date=TODAY)
    repo.set_task_done(done, True)
    repo.add_task("Report", 90, plan_date=TODAY)
    repo.add_task("Tomorrow's existing", 25, plan_date=THU)
    repo.add_capture("Book the dentist")
    repo.add_capture("Idea: weekly digest")
    h1 = repo.add_habit("Read")
    repo.add_habit("Walk")
    repo.add_habit("Mondays only", days=1)
    repo.set_habit_status(h1, TODAY, "done")
    show(ictx, page="focus")
    yield ictx


def open_sheet(ctx: AppContext) -> WrapUpSheet:
    sheet = WrapUpSheet(ctx)
    win_of(ctx).show_sheet(sheet)
    pump()
    return sheet


def titles(rows: list[Any]) -> list[str]:
    return [r.title.full_text() for r in rows]


def test_next_day_word() -> None:
    assert next_day_word(TODAY, THU) == "tomorrow"
    assert next_day_word(date(2026, 10, 16), date(2026, 10, 19)) == "Monday"


def test_summary_sections_and_copy(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    assert sheet.title_text() == "Wrap up the day"
    s = sheet.summary
    assert s.focus.value.text() == "1h" and s.focus.suffix.text() == "/ 2h"
    assert sheet.summary.goal_bar.fraction() == pytest.approx(0.5)
    assert s.sessions.value.text() == "2"  # abandoned not counted
    assert s.habits.value.text() == "1" and s.habits.suffix.text() == "/ 2"
    assert titles(sheet.task_rows) == ["Old one", "Slides", "Report"]
    assert titles(sheet.capture_rows) == ["Book the dentist", "Idea: weekly digest"]
    assert sheet.move_all_button.text() == "Move all to tomorrow"
    assert not sheet.all_clear.isVisible()
    assert sheet.next_day == THU
    assert sheet.went_well.line_edit.accessibleName() == "One thing that went well"
    assert sheet.first_block.line_edit.accessibleName() == "Tomorrow's first block"


def test_move_one_to_tomorrow_and_undo(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    seen: list[str] = []
    wctx.events.data_changed.connect(seen.append)
    row = sheet.task_rows[1]
    tid = row.task_id
    before = wctx.repo.get_task(tid)
    sheet.move_to_next_day(tid)
    moved = wctx.repo.get_task(tid)
    assert moved.plan_date == THU and moved.position == 1  # appended after the existing one
    assert titles(sheet.task_rows) == ["Old one", "Report"] and "tasks" in seen
    toasts = win_of(wctx).toasts
    assert toasts.text() == "Moved ‘Slides’ to tomorrow" and toasts.action_text() == "Undo"
    toasts.trigger_action()
    pump()
    back = wctx.repo.get_task(tid)
    assert back.plan_date == before.plan_date and back.position == before.position
    assert titles(sheet.task_rows) == ["Old one", "Slides", "Report"]


def test_move_all_and_undo(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    ids = [r.task_id for r in sheet.task_rows]
    sheet.move_all_button.click()
    assert [wctx.repo.get_task(i).plan_date for i in ids] == [THU] * 3
    assert sheet.task_rows == [] and not sheet.tasks_header.isVisible()
    assert [t.title for t in wctx.repo.tasks_for_date(THU)] == ["Tomorrow's existing", "Old one", "Slides", "Report"]
    assert win_of(wctx).toasts.text() == "Moved 3 tasks to tomorrow"
    win_of(wctx).toasts.trigger_action()
    pump()
    assert wctx.repo.get_task(ids[0]).plan_date == TODAY - timedelta(days=2)
    assert titles(sheet.task_rows) == ["Old one", "Slides", "Report"]


def test_drop_and_undo(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    tid = sheet.task_rows[2].task_id
    sheet.drop_task(tid)
    assert wctx.repo.get_task(tid) is None
    assert titles(sheet.task_rows) == ["Old one", "Slides"]
    assert win_of(wctx).toasts.text() == "Dropped ‘Report’"
    win_of(wctx).toasts.trigger_action()
    pump()
    restored = [t for t in wctx.repo.tasks_for_date(TODAY) if t.title == "Report"]
    assert len(restored) == 1 and restored[0].minutes == 90 and not restored[0].done
    assert titles(sheet.task_rows) == ["Old one", "Slides", "Report"]


def test_row_buttons_are_wired(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    row = sheet.task_rows[0]
    buttons = [w for w in row.findChildren(type(sheet.done_button)) if w.text() in ("Tomorrow", "Drop")]
    assert [b.text() for b in buttons] == ["Tomorrow", "Drop"]
    assert buttons[0].accessibleName() == "Move ‘Old one’ to tomorrow"
    buttons[0].click()
    pump()
    assert titles(sheet.task_rows) == ["Slides", "Report"]


def test_capture_to_tomorrow_and_undo(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    seen: list[str] = []
    wctx.events.data_changed.connect(seen.append)
    cid = sheet.capture_rows[0].capture_id
    sheet.capture_to_next_day(cid)
    assert [c.text for c in wctx.repo.open_captures()] == ["Idea: weekly digest"]
    assert [t.title for t in wctx.repo.tasks_for_date(THU)] == ["Tomorrow's existing", "Book the dentist"]
    assert {"tasks", "captures"} <= set(seen)
    assert win_of(wctx).toasts.text() == "Added to tomorrow's Up next"
    win_of(wctx).toasts.trigger_action()
    pump()
    assert [t.title for t in wctx.repo.tasks_for_date(THU)] == ["Tomorrow's existing"]
    assert len(wctx.repo.open_captures()) == 2
    assert titles(sheet.capture_rows) == ["Book the dentist", "Idea: weekly digest"]


def test_clear_capture_and_undo(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    cid = sheet.capture_rows[1].capture_id
    sheet.clear_capture(cid)
    assert [c.id for c in wctx.repo.open_captures()] != [] and cid not in [c.id for c in wctx.repo.open_captures()]
    assert titles(sheet.capture_rows) == ["Book the dentist"]
    win_of(wctx).toasts.trigger_action()
    pump()
    assert cid in [c.id for c in wctx.repo.open_captures()]


def test_all_clear_state(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    sheet.move_all_to_next_day()
    for row in list(sheet.capture_rows):
        sheet.clear_capture(row.capture_id)
    pump()
    assert sheet.all_clear.isVisible()
    assert not sheet.captures_header.isVisible() and not sheet.tasks_header.isVisible()


def test_shutdown_saves_note_first_block_and_done_for_today(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    seen: list[str] = []
    wctx.events.data_changed.connect(seen.append)
    sheet.went_well.set_text("  Shipped the fix  ")
    sheet.first_block.set_text("Write the release notes")
    assert sheet.first_block_minutes() == 60  # settings.default_minutes
    sheet.cycle_first_minutes()
    assert sheet.first_block_minutes() == 90 and sheet.first_minutes.text() == "90m"
    sheet.done_button.click()
    pump(10)
    assert wctx.repo.get_day_note(TODAY) == "Shipped the fix"
    first = wctx.repo.tasks_for_date(THU)[0]
    assert first.title == "Write the release notes" and first.position == 0 and first.minutes == 90
    assert [t.title for t in wctx.repo.tasks_for_date(THU)] == ["Write the release notes", "Tomorrow's existing"]
    assert "tasks" in seen
    assert wctx.nudges is not None and wctx.nudges.is_done_for_today
    assert not win_of(wctx).has_sheet()
    assert win_of(wctx).toasts.text() == "Shutdown complete — see you tomorrow."


def test_shutdown_with_empty_fields_creates_nothing(wctx: AppContext) -> None:
    before = len(wctx.repo.tasks_for_date(THU))
    sheet = open_sheet(wctx)
    sheet.shutdown()
    pump()
    assert len(wctx.repo.tasks_for_date(THU)) == before
    assert wctx.repo.get_day_note(TODAY) is None


def test_existing_note_prefilled_and_clearable(wctx: AppContext) -> None:
    wctx.repo.set_day_note(TODAY, "Good morning block")
    sheet = open_sheet(wctx)
    assert sheet.went_well.text() == "Good morning block"
    sheet.went_well.set_text("")
    sheet.shutdown()
    pump()
    assert wctx.repo.get_day_note(TODAY) is None


def test_not_now_discards_fields(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    sheet.first_block.set_text("Something")
    sheet.cancel_button.click()
    pump(10)
    assert not win_of(wctx).has_sheet()
    assert [t.title for t in wctx.repo.tasks_for_date(THU)] == ["Tomorrow's existing"]
    assert wctx.nudges is not None and not wctx.nudges.is_done_for_today


def test_enter_in_first_block_completes(wctx: AppContext) -> None:
    sheet = open_sheet(wctx)
    sheet.first_block.set_text("Plan the sprint")
    sheet.first_block.returnPressed.emit()
    pump(10)
    assert wctx.repo.tasks_for_date(THU)[0].title == "Plan the sprint"
    assert not win_of(wctx).has_sheet()
    assert sheet.primary_button() is sheet.done_button


def test_friday_targets_monday(ictx: AppContext) -> None:
    ictx.clock.set(datetime(2026, 10, 16, 18, 0))  # type: ignore[attr-defined]
    ictx.repo.add_task("Loose end", 25, plan_date=date(2026, 10, 16))
    show(ictx, page="focus")
    sheet = open_sheet(ictx)
    assert sheet.next_day == date(2026, 10, 19)
    assert sheet.move_all_button.text() == "Move all to Monday"
    assert sheet.first_block.line_edit.accessibleName() == "Monday's first block"
    assert "Mon 19 Oct" in sheet.first_hint.text()
    sheet.move_to_next_day(sheet.task_rows[0].task_id)
    assert [t.title for t in ictx.repo.tasks_for_date(date(2026, 10, 19))] == ["Loose end"]
    sheet.first_block.set_text("Monday kickoff")
    sheet.shutdown()
    pump()
    assert ictx.repo.tasks_for_date(date(2026, 10, 19))[0].title == "Monday kickoff"


def test_custom_work_days_and_logical_today(ictx: AppContext) -> None:
    ictx.settings.update(work_days=0b1111111)
    ictx.clock.set(datetime(2026, 10, 17, 1, 30))  # Saturday 01:30 → logical Friday 16 Oct
    sheet = WrapUpSheet(ictx)
    assert sheet.next_day == date(2026, 10, 17)  # every day is a work day → tomorrow = Saturday
    assert sheet.move_all_button.text() == "Move all to tomorrow"
    sheet.deleteLater()


def test_works_without_nudge_service(ictx: AppContext) -> None:
    ictx.nudges = None
    show(ictx, page="focus")
    sheet = open_sheet(ictx)
    sheet.shutdown()
    pump()
    assert not win_of(ictx).has_sheet()


def test_open_wrap_up_reuses_an_open_or_queued_sheet(wctx: AppContext) -> None:
    """Footer link, tray menu and the after-work prompt never stack two wrap-up sheets."""
    from madhura.ui.widgets.sheet import ConfirmSheet

    win = win_of(wctx)
    blocker = ConfirmSheet("Something else", "First in the queue")
    win.show_sheet(blocker)
    first = open_wrap_up(wctx)
    assert open_wrap_up(wctx) is first  # queued → reused
    assert sum(isinstance(s, WrapUpSheet) for s in win.sheet_host.queued()) == 1
    blocker.reject()
    pump()
    assert win.sheet_host.current() is first
    assert open_wrap_up(wctx, show_main=False) is first  # open → reused
    assert not win.sheet_host.queued()
    first.close_sheet()
    pump()
    assert open_wrap_up(wctx) is not first
