"""[S] First-run onboarding sheet (spec §1.11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_s_support import (  # noqa: F401
    LEGACY_FIXTURE,
    FakeAutostart,
    _isolated_xdg,
    fake_autostart,
    fresh_shell,
    key,
    pump,
    win_of,
)

from madhura import app as appmod
from madhura.context import AppContext
from madhura.ui.overlays.onboarding import EMPTY_TASK_ERROR, GOAL_OPTIONS, STEP_TITLES, ChoiceCards, OnboardingSheet, StepDots

K = Qt.Key


def _open(ctx: AppContext, **kw) -> OnboardingSheet:
    win = win_of(ctx)
    win.resize(1120, 760)
    win.show_main()
    pump()
    sheet = OnboardingSheet(ctx, legacy_paths=kw.pop("legacy_paths", []), **kw)
    win.show_sheet(sheet)
    pump(10)
    assert win.sheet_host.current() is sheet
    return sheet


def test_after_show_presents_onboarding_when_not_onboarded(fresh_shell: AppContext) -> None:
    win = win_of(fresh_shell)
    win.show_main()
    appmod.after_show(fresh_shell)
    pump(10)
    assert isinstance(win.sheet_host.current(), OnboardingSheet)
    assert OnboardingSheet.dismissible is False


def test_step_flow_back_next_and_dots(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell)
    assert len(STEP_TITLES) == 4
    assert sheet.step() == 0 and sheet.dots.current() == 0
    assert sheet.back_button.isHidden() and sheet.skip_button.isHidden()
    assert sheet.next_button.text() == "Get started"
    assert sheet.primary_button() is sheet.next_button
    sheet.next_button.click()
    assert sheet.step() == 1 and sheet.dots.current() == 1
    assert not sheet.back_button.isHidden() and sheet.next_button.text() == "Next"
    assert sheet.dots.accessibleName() == "Step 2 of 4"
    sheet.next()
    sheet.next()
    assert sheet.step() == 3
    assert sheet.next_button.isHidden() and not sheet.skip_button.isHidden()
    assert sheet.primary_button() is sheet.lock_in_button
    sheet.next()  # no step 5
    assert sheet.step() == 3
    sheet.back_button.click()
    assert sheet.step() == 2 and sheet.stack.currentIndex() == 2
    sheet.back()
    sheet.back()
    sheet.back()
    assert sheet.step() == 0


def test_escape_is_ignored(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell)
    win = win_of(fresh_shell)
    win.activateWindow()
    sheet.next_button.setFocus()
    QTest.keyClick(sheet.next_button, K.Key_Escape)
    pump(10)
    assert win.sheet_host.current() is sheet
    sheet.next()
    sheet.next()
    sheet.next()
    QTest.keyClick(sheet.task_field.line_edit, K.Key_Escape)
    pump(10)
    assert win.sheet_host.current() is sheet and fresh_shell.settings.get("onboarded") is False


def test_enter_advances_steps(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell)
    key(sheet, K.Key_Return)
    assert sheet.step() == 1
    key(sheet, K.Key_Enter)
    assert sheet.step() == 2
    key(sheet, K.Key_Return)
    assert sheet.step() == 3
    key(sheet, K.Key_Return)  # last step: Enter belongs to the task field / Lock in
    assert sheet.step() == 3
    # Enter on a focused footer button activates that button (Back goes back).
    win_of(fresh_shell).activateWindow()
    sheet.back_button.setFocus()
    key(sheet, K.Key_Return)
    assert sheet.step() == 2


def test_skip_applies_choices_and_finishes(fresh_shell: AppContext, fake_autostart: FakeAutostart) -> None:
    sheet = _open(fresh_shell)
    win = win_of(fresh_shell)
    assert [v for v, _l in GOAL_OPTIONS] == [60, 120, 180, 240]
    assert sheet.goal_chips.value() == 120  # 2h default
    assert sheet.autostart_toggle.isChecked() and sheet.autostart_supported
    sheet.next()
    sheet.goal_chips.setFocus()
    key(sheet.goal_chips, K.Key_Right)
    assert sheet.goal_chips.value() == 180
    sheet.work_days.setFocus()
    key(sheet.work_days, K.Key_End)
    key(sheet.work_days, K.Key_Space)  # add Sunday
    sheet.work_start.set_time_str("08:00")
    sheet.work_end.set_time_str("16:30")
    sheet.next()
    key(sheet.nudge_cards, K.Key_Down)
    assert sheet.nudge_cards.value() == "firm"
    sheet.next()
    sheet.skip_button.click()
    pump(10)
    s = fresh_shell.settings
    assert s.get("onboarded") is True
    assert s.get("daily_goal_minutes") == 180
    assert s.get("work_days") == 31 | 64
    assert (s.get("work_start"), s.get("work_end")) == ("08:00", "16:30")
    assert s.get("nudge_mode") == "firm"
    assert fake_autostart.calls == [True]
    assert str(fresh_shell.engine.state) == "idle" and sheet.started_session_id is None
    assert not win.has_sheet()


def test_nothing_is_saved_before_finishing(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell)
    sheet.next()
    key(sheet.goal_chips, K.Key_Right)
    assert fresh_shell.settings.get("daily_goal_minutes") == 120
    assert fresh_shell.settings.get("onboarded") is False


def test_autostart_toggle_off_and_unsupported(fresh_shell: AppContext, fake_autostart: FakeAutostart, qapp) -> None:
    sheet = _open(fresh_shell)
    sheet.autostart_toggle.click()
    assert not sheet.autostart_toggle.isChecked()
    sheet.finish()
    assert fake_autostart.calls == []  # it was already off: nothing to change

    fake_autostart.supported = False
    other = OnboardingSheet(fresh_shell, legacy_paths=[])
    assert other.autostart_box.isHidden() and not other.autostart_supported
    other._done = False  # noqa: SLF001
    other.finish()
    assert fake_autostart.calls == []
    other.deleteLater()


def test_autostart_failure_is_reported(fresh_shell: AppContext, fake_autostart: FakeAutostart) -> None:
    fake_autostart.succeed = False
    sheet = _open(fresh_shell)
    sheet.finish()
    pump(10)
    assert fresh_shell.settings.get("onboarded") is True
    assert "launch at login" in win_of(fresh_shell).toasts.text()


def test_lock_in_requires_a_task_then_starts_the_first_block(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell)
    for _ in range(3):
        sheet.next()
    assert sheet.duration_chips.value() == 25  # 25m preselected
    assert sheet.preview.text() == "Ends at 10:25 · check-ins every 7 min"
    sheet.lock_in_button.click()
    assert sheet.task_field.error_text() == EMPTY_TASK_ERROR
    assert win_of(fresh_shell).sheet_host.current() is sheet
    assert fresh_shell.settings.get("onboarded") is False
    sheet.task_field.set_text("  Draft   the intro ")
    sheet.duration_chips.setFocus()
    key(sheet.duration_chips, K.Key_Right)
    assert sheet.duration_chips.value() == 45
    assert sheet.preview.text().startswith("Ends at 10:45")
    sheet.lock_in_button.click()
    pump(10)
    engine = fresh_shell.engine
    assert str(engine.state) == "focus"
    assert engine.task == "Draft the intro" and engine.planned_seconds == 45 * 60
    assert sheet.started_session_id == engine.session_id
    assert fresh_shell.settings.get("onboarded") is True
    assert not win_of(fresh_shell).has_sheet()


def test_enter_in_task_field_and_ctrl_enter_lock_in(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell)
    for _ in range(3):
        sheet.next()
    sheet.task_field.set_text("Write tests")
    win = win_of(fresh_shell)
    win.activateWindow()
    sheet.task_field.line_edit.setFocus()
    QTest.keyClick(sheet.task_field.line_edit, K.Key_Return, Qt.KeyboardModifier.ControlModifier)
    pump(10)
    assert str(fresh_shell.engine.state) == "focus"
    assert fresh_shell.engine.task == "Write tests"


def test_preview_reflects_checkins_off_and_stay_open(fresh_shell: AppContext) -> None:
    fresh_shell.settings.update(checkin_enabled=False, after_start="stay")
    sheet = _open(fresh_shell)
    for _ in range(3):
        sheet.next()
    assert sheet.preview.text().endswith("check-ins off")
    titles = [t.text() for _m, t, _b in sheet.next_steps.rows]
    assert titles == ["The timer stays in view", "Check-ins are off"]


def test_legacy_import_toggle(fresh_shell: AppContext, tmp_path: Path) -> None:
    sheet = _open(fresh_shell, legacy_paths=[tmp_path / "missing.json", LEGACY_FIXTURE])
    assert not sheet.legacy_box.isHidden()
    assert sheet.legacy_count == 8
    assert sheet.legacy_title.text() == "Import 8 habits from old Madhura"
    assert sheet.legacy_toggle.isChecked()
    sheet.finish()
    pump(10)
    assert len(fresh_shell.repo.habits()) == 8 and sheet.imported_count == 8
    assert list((fresh_shell.data_dir / "backups").glob("madhura-pre-import-*.db"))
    assert win_of(fresh_shell).toasts.text() == "Imported 8 habits from old Madhura"


def test_legacy_import_toggle_off(fresh_shell: AppContext) -> None:
    sheet = _open(fresh_shell, legacy_paths=[LEGACY_FIXTURE])
    sheet.legacy_toggle.click()
    sheet.finish()
    assert fresh_shell.repo.habits() == []


def test_no_legacy_box_without_new_habits(fresh_shell: AppContext) -> None:
    from madhura.data.legacy_import import import_legacy_habits

    import_legacy_habits(LEGACY_FIXTURE, fresh_shell.repo)
    sheet = _open(fresh_shell, legacy_paths=[LEGACY_FIXTURE])
    assert sheet.legacy_box.isHidden() and sheet.legacy_path is None


def test_default_detection_uses_find_legacy_habits(fresh_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from madhura.data import legacy_import

    monkeypatch.setattr(legacy_import, "find_legacy_habits", lambda: [LEGACY_FIXTURE])
    sheet = OnboardingSheet(fresh_shell)
    assert sheet.legacy_path == LEGACY_FIXTURE
    sheet.deleteLater()


def test_finish_is_idempotent(fresh_shell: AppContext, fake_autostart: FakeAutostart) -> None:
    sheet = _open(fresh_shell, legacy_paths=[LEGACY_FIXTURE])
    sheet.finish()
    sheet.finish()
    assert fake_autostart.calls == [True]
    assert len(fresh_shell.repo.habits()) == 8


def test_choice_cards_keyboard_and_mouse(qapp, fresh_shell: AppContext, qtbot) -> None:
    from madhura.ui.pages._settings_kit import NUDGE_CHOICES

    cards = ChoiceCards(NUDGE_CHOICES, "gentle")
    cards.resize(460, 240)
    cards.show()
    pump()
    changes: list[object] = []
    cards.valueChanged.connect(changes.append)
    key(cards, K.Key_Down)
    key(cards, K.Key_Down)
    assert cards.value() == "off" and changes == ["firm", "off"]
    key(cards, K.Key_Down)  # wraps
    assert cards.value() == "gentle"
    key(cards, K.Key_End)
    assert cards.value() == "off"
    target = cards.cards()[1]
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton)
    assert cards.value() == "firm"
    assert "Firm" in cards.accessibleDescription()
    cards.set_value("gentle")
    assert cards.value() == "gentle" and changes[-1] == "firm"
    cards.deleteLater()


def test_step_dots_paint_in_both_themes(fresh_shell: AppContext) -> None:
    dots = StepDots(4)
    for scheme in ("light", "dark"):
        fresh_shell.settings.update(theme=scheme)
        dots.set_current(2)
        assert not dots.grab().isNull()
    dots.set_current(9)
    assert dots.current() == 3
    dots.deleteLater()


def test_onboarding_renders_each_step_light(fresh_shell: AppContext) -> None:
    fresh_shell.settings.update(theme="light")
    sheet = _open(fresh_shell, legacy_paths=[LEGACY_FIXTURE])
    for _ in range(4):
        assert not win_of(fresh_shell).grab().isNull()
        sheet.next()
    QApplication.processEvents()


@pytest.mark.parametrize("presets", [[15, 25, 45, 60, 90], [45, 75, 105, 135, 165, 195]])
def test_sheet_fits_without_scrollbar(fresh_shell: AppContext, presets: list[int]) -> None:
    fresh_shell.settings.update(duration_presets=presets, time_format="12h")
    sheet = _open(fresh_shell)
    scroll = win_of(fresh_shell).sheet_host._card.scroll  # noqa: SLF001
    for _ in range(4):
        pump(5)
        assert not scroll.verticalScrollBar().isVisible()
        assert scroll.viewport().width() == OnboardingSheet.sheet_width
        sheet.next()
    assert sheet.duration_chips.chip_rect(presets[-1]).right() < sheet.lock_in_button.x()
