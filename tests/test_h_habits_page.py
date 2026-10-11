"""Habits page + editor sheet [H] (spec §1.8)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from test_h_support import (
    TODAY,
    add_habit,
    add_session,
    habits_page,
    hshell_context,
    key,
    log,
    pump,
    set_now,
    win_of,
)

from madhura.context import AppContext
from madhura.core.clock import FakeClock
from madhura.ui import dialogs
from madhura.ui.pages import habits_page as hp
from madhura.ui.pages._habit_kit import CURATED_EMOJI, first_grapheme, fmt_hhmm
from madhura.ui.pages.habit_editor import SUGGESTIONS, HabitEditorSheet, suggestion_for
from madhura.ui.widgets.sheet import ConfirmSheet


@pytest.fixture
def hshell(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> Iterator[AppContext]:
    """Running shell for habits tests (see ``test_h_support.hshell_context``)."""
    yield from hshell_context(qapp, data_dir, fake_clock)


def _status(ctx: AppContext, hid: int, d: date = TODAY) -> str | None:
    for lg in ctx.repo.habit_logs_for_date(d):
        if lg.habit_id == hid:
            return str(lg.status)
    return None


def _seed_groups(ctx: AppContext) -> dict[str, int]:
    return {
        "walk": add_habit(ctx, "Morning walk", time="07:00", emoji="🚶"),
        "meds": add_habit(ctx, "Take meds", time="13:00"),
        "plan": add_habit(ctx, "Plan tomorrow", time="18:30", cue="After dinner"),
        "read": add_habit(ctx, "Read 10 pages"),
        "run": add_habit(ctx, "Long run", time="08:00", days=0b1100000),  # weekends → not today
    }


# =========================================================================== rendering


def test_page_groups_rows_and_not_today_disclosure(hshell: AppContext) -> None:
    ctx = hshell
    ids = _seed_groups(ctx)
    page = habits_page(ctx)
    assert page.group_titles() == ["Morning", "Afternoon", "Evening", "Anytime"]
    assert [r.habit_id for r in page.rows()] == [ids["walk"], ids["meds"], ids["plan"], ids["read"]]
    assert page.not_today_toggle is not None and page.not_today_toggle.text() == "Not today (1)"
    assert page.row(ids["run"]) is None
    page.not_today_toggle.click()
    pump()
    assert page.row(ids["run"]) is not None
    assert page.model(ids["run"]).scheduled is False
    assert page.model(ids["run"]).caption == "Weekends · 08:00"
    page.not_today_toggle.click()
    assert page.row(ids["run"]) is None
    assert page.summary.value_text() == "0 of 4"
    assert not page.is_empty() and page.new_button.isVisible()


def test_row_captions(hshell: AppContext) -> None:
    ctx = hshell
    ctx.settings.update(time_format="24h")
    ids = _seed_groups(ctx)
    quiet = add_habit(ctx, "Stretch", time="21:00", reminder=False)
    for hid in ids.values():
        log(ctx, hid, TODAY - timedelta(days=1), "done")
    log(ctx, quiet, TODAY - timedelta(days=1), "done")
    page = habits_page(ctx)
    assert page.model(ids["plan"]).caption == "After dinner · 18:30"
    assert page.model(ids["read"]).caption == "Anytime"
    assert page.model(quiet).caption == "21:00 · reminder off"
    # 07:00 has passed (it's 10:00) and nothing is logged → pending
    assert page.model(ids["walk"]).caption == "07:00 · Pending"
    ctx.settings.update(time_format="12h")
    pump()
    page.refresh()
    assert page.model(ids["plan"]).caption == "After dinner · 6:30 PM"


def test_never_miss_twice_caption_in_warning(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Journal")
    log(ctx, hid, TODAY - timedelta(days=2), "done")
    page = habits_page(ctx)
    m = page.model(hid)
    assert m.missed_previous and m.caption == "Missed yesterday · don't miss twice" and m.caption_tone == "warning"
    assert page.row(hid).caption_label.property("tone") == "warning"
    # done today → back to normal
    page.toggle_done(hid)
    pump()
    assert page.model(hid).caption == "Anytime" and not page.model(hid).caption_tone
    # an M/W/F habit names the missed weekday
    mwf = add_habit(ctx, "Gym", days=0b0010101)
    page.refresh()
    assert page.model(mwf).caption == "Missed Monday · don't miss twice"


def test_auto_habit_progress_caption_and_badge(hshell: AppContext) -> None:
    ctx = hshell
    deep = add_habit(ctx, "Deep work", focus_minutes=60)
    thesis = add_habit(ctx, "Thesis", focus_minutes=30, project="Thesis")
    add_session(ctx, datetime(2026, 10, 14, 8, 0), 35, project="thesis")
    add_session(ctx, datetime(2026, 10, 14, 9, 0), 20, outcome="abandoned", project="Thesis")
    add_session(ctx, datetime(2026, 10, 13, 9, 0), 50)  # yesterday doesn't count
    page = habits_page(ctx)
    assert page.model(deep).caption == "35 / 60 min deep work"
    assert page.model(thesis).caption == "35 / 30 min on Thesis"
    assert page.row(deep).auto_badge.isVisibleTo(page.row(deep))
    plain = add_habit(ctx, "Plain")
    page.refresh()
    assert not page.row(plain).auto_badge.isVisibleTo(page.row(plain))


def test_last7_dots_and_streak_chip(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Meditate")
    for i in range(1, 4):
        log(ctx, hid, TODAY - timedelta(days=i), "done")
    log(ctx, hid, TODAY - timedelta(days=4), "skipped")
    page = habits_page(ctx)
    row = page.row(hid)
    states = [s for _d, s in row.dots.days()]
    assert states == ["missed", "missed", "skipped", "done", "done", "done", "pending"]
    assert row.dots.tooltip_for(6).endswith("· Not yet")
    assert row.dots.tooltip_for(3) == "Sun 11 Oct · Done"
    assert row.streak.count() == 3 and row.streak.is_shown()
    assert "3-day streak" in row.streak.toolTip()
    fresh = add_habit(ctx, "Fresh")
    page.refresh()
    assert page.row(fresh).streak.count() == 0 and not page.row(fresh).streak.is_shown()


def test_empty_state_and_suggestion_opens_prefilled_editor(hshell: AppContext) -> None:
    ctx = hshell
    page = habits_page(ctx)
    assert page.is_empty()
    assert not page.new_button.isVisibleTo(page) and not page.summary.isVisibleTo(page)
    assert page.empty._title.text() == hp.EMPTY_TITLE  # noqa: SLF001
    assert "37x" in page.empty._body.text()  # noqa: SLF001
    assert [c.text().split("  ", 1)[1] for c in page.suggestions.chips] == [s.name for s in SUGGESTIONS]
    assert [s.name for s in SUGGESTIONS[:3]] == ["Work deeply on your main project", "Ship something small", "Learn and apply one concept"]
    page.suggestions.chips[0].click()
    pump(10)
    sheet = win_of(ctx).sheet_host.current()
    assert isinstance(sheet, HabitEditorSheet) and not sheet.is_editing()
    assert sheet.name_field.text() == "Work deeply on your main project"
    assert sheet.focus_toggle.isChecked() and sheet.minutes.int_value() == 60
    assert sheet.save() is not None
    pump(10)
    assert not page.is_empty() and len(page.rows()) == 1
    h = ctx.repo.habits()[0]
    assert h.focus_minutes == 60 and h.emoji == "🎯"


# =========================================================================== interactions


def test_check_click_toggles_done_and_updates_summary(hshell: AppContext, qtbot) -> None:  # noqa: ANN001
    ctx = hshell
    ids = _seed_groups(ctx)
    page = habits_page(ctx)
    row = page.row(ids["meds"])
    seen: list[str] = []
    ctx.events.data_changed.connect(seen.append)
    qtbot.mouseClick(row.check, Qt.MouseButton.LeftButton)
    pump()
    assert _status(ctx, ids["meds"]) == "done" and "habits" in seen
    assert page.row(ids["meds"]) is row  # updated in place, not rebuilt
    assert row.check.isChecked() and row.name_label.property("tone") == "muted"
    assert page.summary.value_text() == "1 of 4"
    qtbot.mouseClick(row.check, Qt.MouseButton.LeftButton)
    pump()
    assert _status(ctx, ids["meds"]) is None and not row.check.isChecked()
    assert page.summary.value_text() == "0 of 4"


def test_keyboard_navigation_space_skip_edit(hshell: AppContext) -> None:
    ctx = hshell
    ids = _seed_groups(ctx)
    page = habits_page(ctx)
    order = [r.habit_id for r in page.rows()]
    key(page, Qt.Key.Key_Down)
    first = page.row(order[0])
    assert first.hasFocus() and first.is_selected()
    key(first, Qt.Key.Key_Down)
    second = page.row(order[1])
    assert second.hasFocus() and not first.is_selected()
    key(second, Qt.Key.Key_Up)
    assert first.hasFocus()
    key(first, Qt.Key.Key_End)
    assert page.row(order[-1]).hasFocus()
    key(page.row(order[-1]), Qt.Key.Key_Home)
    assert first.hasFocus()
    # Space → done (focus stays on the row)
    key(first, Qt.Key.Key_Space)
    pump()
    assert _status(ctx, order[0]) == "done" and first.hasFocus() and first.check.isChecked()
    key(first, Qt.Key.Key_Space)
    assert _status(ctx, order[0]) is None
    # S → skip / unskip
    key(first, Qt.Key.Key_S)
    pump()
    assert _status(ctx, order[0]) == "skipped"
    assert first.check.is_skipped() and page.model(order[0]).caption == "Skipped today"
    assert page.summary.value_text() == "0 of 3" and "1 skipped" in page.summary.bar_caption.text()
    key(first, Qt.Key.Key_S)
    assert _status(ctx, order[0]) is None and not first.check.is_skipped()
    # skipped → Space marks done
    page.toggle_skip(order[0])
    key(first, Qt.Key.Key_Space)
    assert _status(ctx, order[0]) == "done"
    # E → editor for that habit
    key(first, Qt.Key.Key_E)
    pump(10)
    sheet = win_of(ctx).sheet_host.current()
    assert isinstance(sheet, HabitEditorSheet) and sheet.habit is not None and sheet.habit.id == order[0]
    assert ids  # silence


def test_all_done_summary(hshell: AppContext) -> None:
    ctx = hshell
    a = add_habit(ctx, "A")
    b = add_habit(ctx, "B")
    page = habits_page(ctx)
    page.toggle_done(a)
    page.toggle_skip(b)
    pump()
    assert page.summary.value_text() == "1 of 1"
    assert page.summary.caption_label.text() == "All done for today"
    assert page.summary.caption_label.property("tone") == "success"
    assert page.summary.bar.states() == ["done", "skipped"]


def test_summary_bar_fills_from_the_left(hshell: AppContext) -> None:
    ctx = hshell
    add_habit(ctx, "Early", time="07:00")
    add_habit(ctx, "Middle", time="12:00")
    late = add_habit(ctx, "Late", time="21:00")
    page = habits_page(ctx)
    page.toggle_done(late)
    pump()
    assert page.summary.value_text() == "1 of 3"
    assert page.summary.bar.states() == ["done", "none", "none"]


def test_nothing_scheduled_today_summary(hshell: AppContext) -> None:
    ctx = hshell
    add_habit(ctx, "Weekend hike", days=0b1100000)
    page = habits_page(ctx)
    assert page.summary.value_text() == "Nothing scheduled today"
    assert page.rows() == []


def test_menu_actions(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Floss", time="21:00")
    page = habits_page(ctx)
    menu = page.open_menu(hid)
    assert menu is not None
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels == ["Edit", "Skip today", "Archive", "Delete…"]
    menu.close()
    page.toggle_skip(hid)
    menu = page.open_menu(hid)
    assert "Unskip" in [a.text() for a in menu.actions()]
    menu.close()


def test_archive_with_undo_toast(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Floss")
    keep = add_habit(ctx, "Water")
    page = habits_page(ctx)
    page.archive(hid)
    pump()
    assert ctx.repo.get_habit(hid).archived is True
    assert page.row(hid) is None and page.row(keep) is not None
    toasts = win_of(ctx).toasts
    assert toasts.text() == "Archived ‘Floss’" and toasts.action_text() == "Undo"
    assert page.archived_toggle.isVisibleTo(page) and page.archived_toggle.text() == "Archived habits (1)"
    toasts.trigger_action()
    pump()
    assert ctx.repo.get_habit(hid).archived is False and page.row(hid) is not None
    # restore from the archived list
    page.archive(hid)
    page._toggle_archived()  # noqa: SLF001
    assert page.archived_card.isVisibleTo(page)
    page.unarchive(hid)
    pump()
    assert page.row(hid) is not None and not page.archived_toggle.isVisibleTo(page)


def test_delete_asks_with_confirm_sheet(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Floss")
    log(ctx, hid, TODAY, "done")
    page = habits_page(ctx)
    page.delete_habit(hid)
    pump(10)
    sheet = win_of(ctx).sheet_host.current()
    assert isinstance(sheet, ConfirmSheet) and "Floss" in sheet.title_text()
    sheet.reject()
    pump(10)
    assert ctx.repo.get_habit(hid) is not None
    page.delete_habit(hid)
    pump(10)
    sheet = win_of(ctx).sheet_host.current()
    assert isinstance(sheet, ConfirmSheet)
    sheet.accept()
    pump(10)
    assert ctx.repo.get_habit(hid) is None and ctx.repo.habit_logs_for_date(TODAY) == []
    assert page.is_empty()
    assert win_of(ctx).toasts.text() == "Deleted ‘Floss’"


def test_delete_key_and_dialog_fallback(hshell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Floss")
    page = habits_page(ctx)
    asked: list[str] = []
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: asked.append(a[1]) or True)
    window = ctx.window
    ctx.window = None
    try:
        key(page.row(hid), Qt.Key.Key_Delete)
    finally:
        ctx.window = window
    assert asked and ctx.repo.get_habit(hid) is None


def test_ctrl_n_on_habits_page_opens_editor(hshell: AppContext) -> None:
    ctx = hshell
    win = habits_page(ctx) and win_of(ctx)
    sc = next(s for s in win.shortcuts() if s.key().toString() == "Ctrl+N")
    sc.activated.emit()
    pump(10)
    assert isinstance(win.sheet_host.current(), HabitEditorSheet)


def test_logical_day_is_used(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Late reading")
    set_now(ctx, datetime(2026, 10, 15, 2, 0))  # 02:00 Thursday → logical Wednesday (day start 4)
    page = habits_page(ctx)
    page.toggle_done(hid)
    assert _status(ctx, hid, date(2026, 10, 14)) == "done"
    assert _status(ctx, hid, date(2026, 10, 15)) is None


def test_habit_created_after_midnight_belongs_to_the_logical_day(hshell: AppContext) -> None:
    ctx = hshell
    set_now(ctx, datetime(2026, 10, 15, 1, 30))  # 01:30 Thursday → logical Wednesday (day start 4)
    hid = add_habit(ctx, "Stretch", created=datetime(2026, 10, 15, 1, 30))
    page = habits_page(ctx)
    m = page.model(hid)
    assert m.scheduled and m.state == "none"
    assert [s for _d, s in m.last7][-2:] == ["unscheduled", "pending"]  # Tue before creation, Wed today
    # next morning, the untouched Wednesday was a real (missed) day of the habit
    set_now(ctx, datetime(2026, 10, 15, 9, 0))
    page.refresh()
    m = page.model(hid)
    assert [s for _d, s in m.last7][-3:] == ["unscheduled", "missed", "pending"]
    assert m.missed_previous and m.caption == "Missed yesterday · don't miss twice"


def test_day_changed_refreshes(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Water")
    page = habits_page(ctx)
    page.toggle_done(hid)
    assert page.row(hid).check.isChecked()
    set_now(ctx, datetime(2026, 10, 15, 9, 0))
    ctx.events.day_changed.emit()
    pump()
    assert not page.row(hid).check.isChecked()
    assert page.row(hid).streak.count() == 1


def test_theme_switch_repaints(hshell: AppContext) -> None:
    ctx = hshell
    _seed_groups(ctx)
    page = habits_page(ctx)
    for scheme in ("light", "dark"):
        ctx.settings.update(theme=scheme)
        pump()
        assert not page.grab().isNull()


# =========================================================================== editor


def test_editor_requires_name_and_a_day(hshell: AppContext) -> None:
    ctx = hshell
    page = habits_page(ctx)
    sheet = page.new_habit()
    pump(10)
    assert sheet.save() is None
    assert sheet.name_field.has_error() and sheet.name_field.error_text() == "Give your habit a name"
    assert ctx.repo.habits() == []
    sheet.name_field.set_text("   ")
    assert sheet.save() is None
    sheet.name_field.set_text("Stretch")
    sheet.day_chips.set_mask(0)
    assert sheet.save() is None and sheet.days_error.isVisibleTo(sheet)
    sheet.day_chips.set_mask(31)
    sheet._on_mask_changed(31)  # noqa: SLF001 - DayChips only emits on user clicks
    assert not sheet.days_error.isVisibleTo(sheet)
    hid = sheet.save()
    assert hid is not None
    h = ctx.repo.get_habit(hid)
    assert h.name == "Stretch" and h.days == 31 and h.time is None and h.focus_minutes is None


def test_editor_reminder_needs_time_and_urgent_follows_reminder(hshell: AppContext) -> None:
    ctx = hshell
    sheet = HabitEditorSheet(ctx)
    assert not sheet.time_toggle.isChecked()
    assert not sheet.reminder_toggle.isEnabled() and not sheet.reminder_toggle.isChecked()
    assert sheet.reminder_row.description.text() == "Reminders need a time"
    # the inactive row reads as inactive (dimmed title, like the urgent row) but its hint stays readable
    assert not sheet.reminder_row.title.isEnabled() and sheet.reminder_row.description.isEnabled()
    assert not sheet.urgent_toggle.isEnabled() and not sheet.urgent_row.title.isEnabled()
    sheet.time_toggle.setChecked(True)
    assert sheet.reminder_toggle.isEnabled() and sheet.reminder_toggle.isChecked()
    assert sheet.reminder_row.title.isEnabled()
    assert sheet.reminder_row.description.text() == "" and sheet.urgent_toggle.isEnabled()
    sheet.reminder_toggle.click()  # user turns reminders off
    assert not sheet.reminder_toggle.isChecked() and not sheet.urgent_toggle.isEnabled()
    sheet.time_toggle.setChecked(False)
    sheet.time_toggle.setChecked(True)
    assert not sheet.reminder_toggle.isChecked()  # the user's choice is remembered


def test_editor_create_full_habit(hshell: AppContext) -> None:
    ctx = hshell
    add_session(ctx, datetime(2026, 10, 14, 8, 0), 25, project="Madhura")
    seen: list[str] = []
    ctx.events.data_changed.connect(seen.append)
    page = habits_page(ctx)
    sheet = page.new_habit()
    pump(10)
    sheet.name_field.set_text("  Ship   a  PR ")
    sheet.cue_field.set_text("After standup")
    sheet.set_emoji("🚀")
    sheet.time_toggle.setChecked(True)
    sheet.time_field.set_time_str("16:45")
    sheet.urgent_toggle.setChecked(True)
    sheet._on_day_preset(31)  # noqa: SLF001 - the "Weekdays" chip
    sheet.focus_toggle.setChecked(True)
    sheet.minutes.set_value(90)
    sheet.project_field.set_text("Madhura")
    saved: list[int] = []
    sheet.saved.connect(saved.append)
    hid = sheet.save()
    pump(10)
    assert saved == [hid] and "habits" in seen
    h = ctx.repo.get_habit(hid)
    assert (h.name, h.emoji, h.cue, h.time, h.days, h.reminder, h.urgent, h.focus_minutes, h.project) == (
        "Ship a PR",
        "🚀",
        "After standup",
        "16:45",
        31,
        True,
        True,
        90,
        "Madhura",
    )
    assert not win_of(ctx).has_sheet()
    assert page.row(hid) is not None


def test_editor_edit_existing_habit(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Read", emoji="📖", cue="Before bed", time="21:30", days=96, reminder=False, focus_minutes=None)
    sheet = HabitEditorSheet(ctx, hid)
    assert sheet.is_editing() and sheet.title_text() == "Edit habit" and sheet.suggestions is None
    assert sheet.name_field.text() == "Read" and sheet.emoji() == "📖" and sheet.cue_field.text() == "Before bed"
    assert sheet.time_toggle.isChecked() and sheet.time_field.time_str() == "21:30"
    assert sheet.day_chips.mask() == 96 and sheet.day_presets.value() == 96
    assert not sheet.reminder_toggle.isChecked()
    assert sheet.save_button.text() == "Save"
    sheet.name_field.set_text("Read 20 pages")
    sheet.time_toggle.setChecked(False)
    assert sheet.save() == hid
    h = ctx.repo.get_habit(hid)
    assert h.name == "Read 20 pages" and h.time is None and h.reminder is False and len(ctx.repo.habits()) == 1
    # legacy positional/keyword aliases from the shell stub still work
    assert HabitEditorSheet(ctx, h).habit.id == hid
    assert HabitEditorSheet(ctx, habit=hid).habit.id == hid
    assert HabitEditorSheet(ctx, prefill={"name": "X"}).name_field.text() == "X"


def test_editor_suggestion_chips_prefill(hshell: AppContext) -> None:
    ctx = hshell
    sheet = HabitEditorSheet(ctx)
    assert sheet.suggestions is not None
    assert [v for v, _l in sheet.suggestions.options()] == [s.name for s in SUGGESTIONS]
    sheet._on_suggestion("Read 10 pages")  # noqa: SLF001 - chipClicked handler
    assert sheet.name_field.text() == "Read 10 pages" and sheet.emoji() == "📖" and sheet.cue_field.text() == "Before bed"
    assert not sheet.focus_toggle.isChecked()
    assert sheet.suggestions.value() is None  # chips act like buttons
    sheet._on_suggestion("Work deeply on your main project")  # noqa: SLF001
    assert sheet.focus_toggle.isChecked() and sheet.minutes.int_value() == 60
    assert sheet.focus_box.isVisibleTo(sheet)
    assert suggestion_for("ship SOMETHING small").emoji == "🚀"
    assert HabitEditorSheet(ctx, preset="Plan tomorrow").name_field.text() == "Plan tomorrow"


def test_editor_opened_with_a_name_places_the_cursor_usefully(hshell: AppContext) -> None:
    """A prefilled name is selected (new habit from a preset) or has the cursor at its end
    (editing) once the sheet focuses it — never the cursor parked before the first letter."""
    ctx = hshell
    page = habits_page(ctx)
    win_of(ctx).activateWindow()
    sheet = page.new_habit("Work deeply on your main project")
    pump(10)
    edit = sheet.name_field.line_edit
    assert edit.hasFocus()
    assert edit.selectedText() == "Work deeply on your main project"
    sheet.close_sheet()
    pump(10)
    hid = add_habit(ctx, "Read 10 pages")
    sheet = page.edit_habit(hid)
    pump(10)
    edit = sheet.name_field.line_edit
    assert edit.hasFocus() and not edit.hasSelectedText()
    assert edit.cursorPosition() == len("Read 10 pages")
    sheet.close_sheet()
    pump(10)
    sheet = page.new_habit()  # empty name: nothing to select
    pump(10)
    assert sheet.name_field.line_edit.hasFocus() and sheet.name_field.text() == ""
    sheet.close_sheet()
    pump(10)


def test_editor_emoji_popover_and_typing(hshell: AppContext) -> None:
    ctx = hshell
    page = habits_page(ctx)
    sheet = page.new_habit()
    pump(10)
    sheet.open_emoji_picker()
    pump()
    pop = sheet.emoji_popover()
    assert pop is not None and pop.is_open()
    picker = pop.content()
    assert len(picker.buttons) == 24 and [b.emoji() for b in picker.buttons] == list(CURATED_EMOJI)
    picker.buttons[2].click()
    pump()
    assert sheet.emoji() == CURATED_EMOJI[2] and sheet.emoji_popover() is None
    sheet.open_emoji_picker()
    picker = sheet.emoji_popover().content()
    assert picker.remove.isVisibleTo(picker)
    picker.field.set_text("👍🏽 thumbs")
    picker._typed()  # noqa: SLF001 - returnPressed handler
    assert sheet.emoji() == "👍🏽"
    sheet.open_emoji_picker()
    sheet.emoji_popover().content().remove.click()
    assert sheet.emoji() == ""
    sheet.name_field.set_text("Hydrate")
    hid = sheet.save()
    assert ctx.repo.get_habit(hid).emoji is None


def test_first_grapheme_and_time_format_helpers() -> None:
    assert first_grapheme("") == "" and first_grapheme("   ") == ""
    assert first_grapheme("abc") == "a"
    assert first_grapheme("👨‍👩‍👧 family") == "👨‍👩‍👧"
    assert first_grapheme("✍️x") == "✍️"
    assert fmt_hhmm("08:05", True) == "08:05" and fmt_hhmm("18:30", False) == "6:30 PM" and fmt_hhmm(None, True) == ""


def test_editor_focus_habit_auto_completes_on_save(hshell: AppContext) -> None:
    ctx = hshell
    add_session(ctx, datetime(2026, 10, 14, 8, 0), 45)
    sheet = HabitEditorSheet(ctx)
    sheet.name_field.set_text("Deep work")
    sheet.focus_toggle.setChecked(True)
    sheet.minutes.set_value(40)
    hid = sheet.save()
    assert _status(ctx, hid) == "done"


def test_editor_sheet_fits_the_window(hshell: AppContext) -> None:
    ctx = hshell
    page = habits_page(ctx)
    sheet = page.new_habit()
    pump(10)
    host = win_of(ctx).sheet_host
    card = host.card_geometry()
    assert card.height() <= host.height() - 64
    # the footer is reachable without scrolling at the default window size
    assert sheet.save_button.mapTo(sheet, sheet.save_button.rect().bottomLeft()).y() <= card.height()
    QApplication.processEvents()


def test_deleting_the_focused_last_habit_parks_focus_on_the_page(hshell: AppContext) -> None:
    ctx = hshell
    hid = add_habit(ctx, "Only one")
    page = habits_page(ctx)
    key(page, Qt.Key.Key_Down)
    assert page.row(hid).hasFocus()
    key(page.row(hid), Qt.Key.Key_Delete)
    pump(10)
    sheet = win_of(ctx).sheet_host.current()
    assert isinstance(sheet, ConfirmSheet)
    sheet.accept()
    pump(20)
    fw = QApplication.focusWidget()
    assert fw is page or (fw is not None and page.isAncestorOf(fw))
    assert not win_of(ctx).sidebar.isAncestorOf(fw)


def test_mouse_click_on_row_focuses_without_keyboard_highlight(hshell: AppContext, qtbot) -> None:  # noqa: ANN001
    ctx = hshell
    hid = add_habit(ctx, "Water")
    page = habits_page(ctx)
    row = page.row(hid)
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton, pos=row.rect().center())
    assert row.hasFocus() and not row.is_selected()
    qtbot.mouseDClick(row, Qt.MouseButton.LeftButton, pos=row.rect().center())
    pump(10)
    assert isinstance(win_of(ctx).sheet_host.current(), HabitEditorSheet)
