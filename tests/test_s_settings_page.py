"""[S] Settings page (spec §1.10): every control writes its setting, follows external changes,
launch-at-login truth, data actions (export, legacy import, restore, reset) and About."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTime
from PySide6.QtWidgets import QApplication
from test_s_support import (  # noqa: F401
    LEGACY_FIXTURE,
    FakeAutostart,
    _isolated_xdg,
    fake_autostart,
    key,
    pump,
    record,
    s_shell,
    settings_page,
    win_of,
)

from madhura import __version__
from madhura.context import AppContext
from madhura.data import backup, legacy_import
from madhura.ui import dialogs, theme
from madhura.ui.pages import settings_page as sp
from madhura.ui.pages._settings_kit import NUDGE_EXPLANATIONS, AccentPicker, backup_label, shorten_path
from madhura.ui.shortcuts_sheet import ShortcutsSheet
from madhura.ui.widgets.sheet import ConfirmSheet

K = Qt.Key
_DEDUPE_WAIT_MS = 600


def _get(ctx: AppContext, k: str) -> object:
    return ctx.settings.get(k)


# =========================================================================== structure


def test_groups_in_spec_order_and_max_width(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    assert list(page.groups) == list(sp.GROUP_TITLES)
    assert page.column.maximumWidth() == 720
    assert page.header.title_label.text() == "Settings"
    # No raw combo/spin boxes anywhere on the page.
    from PySide6.QtWidgets import QComboBox, QSpinBox

    assert not page.findChildren(QComboBox) and not page.findChildren(QSpinBox)


def test_controls_are_named_for_accessibility(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    assert page.controls["checkin_enabled"].accessibleName() == "Check-ins"
    assert page.controls["reduce_motion"].accessibleName() == "Reduce motion"
    assert page.controls["launch_at_login"].accessibleName() == "Launch at login"
    assert page.accent_picker.accessibleName() == "Accent color"
    assert page.volume_slider.accessibleName() == "Volume"
    assert page.work_start.time_edit.accessibleName() == "Work hours start"


# =========================================================================== Focus group


def test_focus_group_controls_write_settings(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    select = page.controls["default_minutes"]
    assert [v for v, _l in select.options()] == [15, 25, 45, 60, 90]
    assert select.current_label() == "1 hour"
    key(select, K.Key_Down)
    assert _get(s_shell, "default_minutes") == 90
    key(select, K.Key_Up)
    key(select, K.Key_Up)
    assert _get(s_shell, "default_minutes") == 45

    goal = page.controls["daily_goal_minutes"]
    assert goal.text() == "2h"
    key(goal, K.Key_Up)
    assert _get(s_shell, "daily_goal_minutes") == 150 and goal.text() == "2h 30m"
    width = goal.width()
    key(goal, K.Key_End)
    assert _get(s_shell, "daily_goal_minutes") == 720
    key(goal, K.Key_Home)
    assert _get(s_shell, "daily_goal_minutes") == 30
    assert goal.width() == width  # stable width: the − button never moves

    seg = page.controls["after_start"]
    key(seg, K.Key_Right)
    assert _get(s_shell, "after_start") == "stay"

    hour = page.controls["day_start_hour"]
    assert hour.current_label() == "04:00" or hour.current_label() == "4:00 AM"
    key(hour, K.Key_Down)
    assert _get(s_shell, "day_start_hour") == 5
    assert hour.options()[0][1] == "Midnight"


def test_default_duration_follows_presets_and_custom_default(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    s_shell.settings.update(default_minutes=35)
    select = page.controls["default_minutes"]
    assert select.value() == 35 and select.current_label() == "35 min"
    s_shell.settings.update(duration_presets=[20, 40, 120])
    assert [v for v, _l in select.options()] == [20, 35, 40, 120]
    assert dict(select.options())[120] == "2 hours"


# =========================================================================== Check-ins & breaks


def test_checkin_controls_and_dependent_rows(s_shell: AppContext, qtbot) -> None:
    page = settings_page(s_shell)
    toggle = page.controls["checkin_enabled"]
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert _get(s_shell, "checkin_enabled") is False
    for k in ("checkin_interval_minutes", "checkin_adaptive", "checkin_timeout_seconds"):
        assert not page.rows[k].isEnabled()
    s_shell.settings.update(checkin_enabled=True)
    assert toggle.isChecked() and page.rows["checkin_interval_minutes"].isEnabled()

    interval = page.controls["checkin_interval_minutes"]
    assert interval.current_label() == "Every 7 min"
    key(interval, K.Key_Down)
    assert _get(s_shell, "checkin_interval_minutes") == 10
    qtbot.mouseClick(page.controls["checkin_adaptive"], Qt.MouseButton.LeftButton)
    assert _get(s_shell, "checkin_adaptive") is False
    timeout = page.controls["checkin_timeout_seconds"]
    key(timeout, K.Key_Down)
    assert _get(s_shell, "checkin_timeout_seconds") == 120


def test_break_controls(s_shell: AppContext, qtbot) -> None:
    page = settings_page(s_shell)
    length = page.controls["break_minutes"]
    assert length.text() == "5 min"
    key(length, K.Key_Up)
    assert _get(s_shell, "break_minutes") == 6
    key(length, K.Key_End)
    assert _get(s_shell, "break_minutes") == 30
    qtbot.mouseClick(page.controls["auto_start_break"], Qt.MouseButton.LeftButton)
    assert _get(s_shell, "auto_start_break") is True
    qtbot.mouseClick(page.controls["breaks_enabled"], Qt.MouseButton.LeftButton)
    assert _get(s_shell, "breaks_enabled") is False
    assert not page.rows["break_minutes"].isEnabled() and not page.rows["auto_start_break"].isEnabled()


# =========================================================================== Accountability


def test_nudge_mode_explanations(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    seg = page.controls["nudge_mode"]
    desc = page.nudge_row.description_label
    assert desc.text() == NUDGE_EXPLANATIONS["gentle"]
    key(seg, K.Key_Right)
    assert _get(s_shell, "nudge_mode") == "firm"
    assert desc.text() == NUDGE_EXPLANATIONS["firm"] and "without stealing focus" in desc.text()
    key(seg, K.Key_Home)
    assert _get(s_shell, "nudge_mode") == "off"
    assert desc.text() == NUDGE_EXPLANATIONS["off"]
    assert not page.rows["nudge_idle_minutes"].isEnabled()
    s_shell.settings.update(nudge_mode="gentle")
    assert seg.value() == "gentle" and page.rows["nudge_idle_minutes"].isEnabled()
    assert set(NUDGE_EXPLANATIONS) == {"off", "gentle", "firm"}


def test_idle_minutes_and_work_days(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    idle = page.controls["nudge_idle_minutes"]
    key(idle, K.Key_PageUp)
    assert _get(s_shell, "nudge_idle_minutes") == 10
    days = page.controls["work_days"]
    days.setFocus()
    key(days, K.Key_End)  # focus Sunday
    key(days, K.Key_Space)
    assert _get(s_shell, "work_days") == 31 | 64
    key(days, K.Key_Home)
    key(days, K.Key_Space)  # Monday off
    assert _get(s_shell, "work_days") == (31 | 64) & ~1
    s_shell.settings.update(work_days=96)
    assert days.mask() == 96


def test_work_hours_fields_and_hint(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    page.work_start.time_edit.setTime(QTime(8, 30))
    assert _get(s_shell, "work_start") == "08:30"
    hint = page.work_hours_row.description_label
    assert "inside these hours" in hint.text()
    page.work_end.time_edit.setTime(QTime(8, 30))
    assert _get(s_shell, "work_end") == "08:30"
    assert "all day" in hint.text()
    page.work_end.time_edit.setTime(QTime(2, 0))
    assert "Overnight" in hint.text()
    s_shell.settings.update(work_start="10:15")
    assert page.work_start.time_str() == "10:15"


# =========================================================================== Mini timer & sound


def test_mini_timer_controls(s_shell: AppContext, qtbot) -> None:
    page = settings_page(s_shell)
    seg = page.controls["pill_mode"]
    assert [lab for _v, lab in seg.options()] == ["When window hidden", "Always", "Never"]
    key(seg, K.Key_End)
    assert _get(s_shell, "pill_mode") == "never"
    qtbot.mouseClick(page.controls["pill_show_seconds"], Qt.MouseButton.LeftButton)
    assert _get(s_shell, "pill_show_seconds") is True


def test_volume_slider_plays_test_on_release(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    page = settings_page(s_shell)
    played: list[str] = []
    monkeypatch.setattr(s_shell.sound, "play", lambda event: played.append(event) or True)
    slider = page.volume_slider
    assert page.volume_label.text() == "40%"
    slider.setSliderDown(True)
    slider.setValue(70)
    assert _get(s_shell, "sound_volume") == 70 and page.volume_label.text() == "70%"
    assert played == []  # not while dragging
    slider.setSliderDown(False)  # sliderReleased → valueReleased
    assert played == ["test"]
    # Keyboard: the test sound plays once the value settles.
    slider.setFocus()
    key(slider, K.Key_Right)
    assert _get(s_shell, "sound_volume") == 71
    qtbot.waitUntil(lambda: len(played) == 2, timeout=2000)
    # Clicking the groove jumps and plays on release.
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() // 4, slider.height() // 2))
    assert 5 < _get(s_shell, "sound_volume") < 40
    assert played == ["test"] * 3  # exactly one chime per release
    qtbot.wait(_DEDUPE_WAIT_MS)
    assert played == ["test"] * 3  # and no late keyboard-path chime either


def test_test_button_and_muted_volume(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    page = settings_page(s_shell)
    played: list[str] = []
    monkeypatch.setattr(s_shell.sound, "play", lambda event: played.append(event) or True)
    qtbot.mouseClick(page.test_sound_button, Qt.MouseButton.LeftButton)
    assert played == ["test"]
    s_shell.settings.update(sound_volume=0)
    assert page.volume_slider.value() == 0 and page.volume_label.text() == "Off"
    qtbot.mouseClick(page.test_sound_button, Qt.MouseButton.LeftButton)
    assert played == ["test"]
    assert "Volume is off" in win_of(s_shell).toasts.text()


@pytest.mark.parametrize("k", ["sound_checkin", "sound_complete", "sound_break_over", "sound_habit", "sound_nudge"])
def test_sound_event_toggles(s_shell: AppContext, qtbot, k: str) -> None:
    page = settings_page(s_shell)
    before = _get(s_shell, k)
    qtbot.mouseClick(page.controls[k], Qt.MouseButton.LeftButton)
    assert _get(s_shell, k) is (not before)
    s_shell.settings.update(**{k: before})
    assert page.controls[k].isChecked() is before


# =========================================================================== Appearance


def test_theme_and_time_format(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    seg = page.controls["theme"]
    key(seg, K.Key_End)
    assert _get(s_shell, "theme") == "light" and s_shell.theme.scheme == "light"
    s_shell.settings.update(theme="dark")
    assert seg.value() == "dark"

    fmt = page.controls["time_format"]
    key(fmt, K.Key_End)
    assert _get(s_shell, "time_format") == "12h"
    assert page.rows["time_format"].description_label.text() == "Times look like 2:35 PM."
    assert page.work_start.time_edit.displayFormat() == "h:mm AP"
    assert page.controls["day_start_hour"].current_label() == "4:00 AM"
    key(fmt, K.Key_Left)
    assert _get(s_shell, "time_format") == "24h"
    assert page.rows["time_format"].description_label.text() == "Times look like 14:35."
    assert page.work_start.time_edit.displayFormat() == "HH:mm"


def test_reduce_motion_toggle(s_shell: AppContext, qtbot) -> None:
    page = settings_page(s_shell)
    toggle = page.controls["reduce_motion"]
    assert toggle.isChecked()  # the test shell turns it on
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert _get(s_shell, "reduce_motion") is False and s_shell.theme.reduce_motion is False
    s_shell.settings.update(reduce_motion=True)
    assert toggle.isChecked()


def test_accent_swatches_keyboard_navigation(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    picker = page.accent_picker
    assert picker.names() == ["indigo", "teal", "rose", "amber", "violet"]
    assert picker.value() == "indigo"
    picker.setFocus(Qt.FocusReason.TabFocusReason)
    key(picker, K.Key_Right)
    assert _get(s_shell, "accent") == "teal" and s_shell.theme.accent == "teal"
    assert picker.accessibleDescription() == "Teal selected"
    key(picker, K.Key_Down)
    assert _get(s_shell, "accent") == "rose"
    key(picker, K.Key_End)
    assert _get(s_shell, "accent") == "violet"
    key(picker, K.Key_Right)  # wraps
    assert _get(s_shell, "accent") == "indigo"
    key(picker, K.Key_Left)
    assert _get(s_shell, "accent") == "violet"
    key(picker, K.Key_Home)
    assert _get(s_shell, "accent") == "indigo"
    assert picker.focus_ring_rect() == picker.swatch_rect(0)
    # External change: silent sync.
    emitted = record(picker.valueChanged)
    s_shell.settings.update(accent="amber")
    assert picker.value() == "amber" and emitted == []


def test_accent_picker_mouse_and_theme_repaint(qapp, s_shell: AppContext, qtbot) -> None:
    picker = AccentPicker("indigo")
    picker.show()
    changes = record(picker.valueChanged)
    qtbot.mouseClick(picker, Qt.MouseButton.LeftButton, pos=picker.swatch_rect(3).center())
    assert picker.value() == "amber" and changes == ["amber"]
    qtbot.mouseClick(picker, Qt.MouseButton.LeftButton, pos=picker.swatch_rect(3).center())
    assert changes == ["amber"]  # same swatch: no new signal
    picker.set_value("bogus")
    assert picker.value() == "amber"
    s_shell.settings.update(theme="light")  # repaints without errors
    pump()
    picker.grab()
    picker.deleteLater()


# =========================================================================== System


def test_launch_at_login_states(s_shell: AppContext, fake_autostart: FakeAutostart, qtbot) -> None:
    page = settings_page(s_shell)
    toggle = page.autostart_toggle
    desc = page.autostart_row.description_label
    assert not toggle.isChecked() and page.repair_button.isHidden()
    assert page.autostart_state() == "off"

    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert fake_autostart.calls == [True] and toggle.isChecked()
    assert page.autostart_state() == "on"

    fake_autostart.state = "disabled_by_os"
    page.on_shown()
    assert not toggle.isChecked()
    assert desc.text().startswith("Disabled in") and desc.property("tone") == "warning"

    fake_autostart.state = "needs_repair"
    page.on_shown()
    assert toggle.isChecked() and not page.repair_button.isHidden()
    qtbot.mouseClick(page.repair_button, Qt.MouseButton.LeftButton)
    assert fake_autostart.calls[-1] is True and fake_autostart.state == "on"
    assert page.repair_button.isHidden() and desc.property("tone") == ""
    assert "repaired" in win_of(s_shell).toasts.text()

    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert fake_autostart.calls[-1] is False and not toggle.isChecked()


def test_launch_at_login_failure_and_unsupported(s_shell: AppContext, fake_autostart: FakeAutostart, qtbot) -> None:
    page = settings_page(s_shell)
    fake_autostart.succeed = False
    qtbot.mouseClick(page.autostart_toggle, Qt.MouseButton.LeftButton)
    assert not page.autostart_toggle.isChecked()  # snapped back to the truth
    assert "Couldn't change launch at login" in win_of(s_shell).toasts.text()
    fake_autostart.supported = False
    page.on_shown()
    assert not page.autostart_toggle.isEnabled()
    assert page.autostart_state() == "unsupported"
    assert "Not available" in page.autostart_row.description_label.text()


def test_tray_toggles_without_a_tray(s_shell: AppContext, qtbot) -> None:
    page = settings_page(s_shell)
    assert not page.controls["close_to_tray"].isEnabled()
    assert "quits Madhura" in page.rows["close_to_tray"].description_label.text()
    qtbot.mouseClick(page.controls["start_minimized"], Qt.MouseButton.LeftButton)
    assert _get(s_shell, "start_minimized") is True
    s_shell.settings.update(close_to_tray=False)
    assert not page.controls["close_to_tray"].isChecked()


# =========================================================================== external changes


def test_every_control_follows_external_changes_without_echo(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    changed = record(s_shell.settings.changed)
    s_shell.settings.update(
        default_minutes=25,
        daily_goal_minutes=180,
        after_start="stay",
        day_start_hour=2,
        checkin_interval_minutes=15,
        checkin_timeout_seconds=30,
        break_minutes=12,
        nudge_idle_minutes=20,
        pill_mode="always",
        sound_volume=55,
        theme="system",
        time_format="24h",
    )
    pump()
    c = page.controls
    assert c["default_minutes"].value() == 25
    assert c["daily_goal_minutes"].value() == 3.0
    assert c["after_start"].value() == "stay"
    assert c["day_start_hour"].value() == 2
    assert c["checkin_interval_minutes"].value() == 15
    assert c["checkin_timeout_seconds"].value() == 30
    assert c["break_minutes"].value() == 12
    assert c["nudge_idle_minutes"].value() == 20
    assert c["pill_mode"].value() == "always"
    assert page.volume_slider.value() == 55 and page.volume_label.text() == "55%"
    assert c["theme"].value() == "system"
    assert c["time_format"].value() == "24h"
    # Each key changed exactly once: the page never wrote anything back.
    assert sorted(changed) == sorted(set(changed))


# =========================================================================== Data


def test_open_data_folder(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    page = settings_page(s_shell)
    opened: list[str] = []
    monkeypatch.setattr(sp, "open_path", lambda p: opened.append(str(p)) or True)
    qtbot.mouseClick(page.open_data_button, Qt.MouseButton.LeftButton)
    assert opened == [str(s_shell.data_dir)]
    assert page.data_folder_row.description_label.text() == shorten_path(s_shell.data_dir)
    monkeypatch.setattr(sp, "open_path", lambda p: False)
    page.open_data_folder()
    assert "Couldn't open" in win_of(s_shell).toasts.text()


def test_export_everything(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qtbot) -> None:
    page = settings_page(s_shell)
    s_shell.repo.add_habit("Read")
    asked: list[str] = []

    def save_path(_parent, _title, default_name="", _filter=""):
        asked.append(default_name)
        return str(tmp_path / "everything")

    monkeypatch.setattr(dialogs, "get_save_path", save_path)
    qtbot.mouseClick(page.export_button, Qt.MouseButton.LeftButton)
    out = tmp_path / "everything.json"
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert asked and asked[0].endswith("madhura-export-2026-10-14.json")
    assert win_of(s_shell).toasts.text() == "Exported to everything.json"
    # Cancelled picker: nothing happens.
    monkeypatch.setattr(dialogs, "get_save_path", lambda *a, **k: None)
    page.export_everything()


def test_export_default_name_uses_logical_day(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    page = settings_page(s_shell)
    s_shell.clock.set(datetime(2026, 10, 15, 2, 30))  # before the 04:00 day start
    asked: list[str] = []
    monkeypatch.setattr(dialogs, "get_save_path", lambda _p, _t, default_name="", _f="": asked.append(default_name))
    page.export_everything()
    assert asked[0].endswith("madhura-export-2026-10-14.json")


def test_export_failure_shows_error(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from madhura.data import export

    page = settings_page(s_shell)
    monkeypatch.setattr(dialogs, "get_save_path", lambda *a, **k: str(tmp_path / "x.json"))

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(export, "export_all_json", denied)
    page.export_everything()
    assert win_of(s_shell).toasts.text() == "Couldn't export — Permission denied."


def test_import_legacy_via_file_picker(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    page = settings_page(s_shell)
    kinds = record(s_shell.events.data_changed)
    monkeypatch.setattr(legacy_import, "find_legacy_habits", lambda: [])
    monkeypatch.setattr(dialogs, "get_open_path", lambda *a, **k: str(LEGACY_FIXTURE))
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert len(s_shell.repo.habits()) == 8
    assert "habits" in kinds
    assert win_of(s_shell).toasts.text() == "Imported 8 habits"
    backups = list((s_shell.data_dir / "backups").glob("madhura-pre-import-*.db"))
    assert len(backups) == 1
    # Second import: nothing new, no extra backup.
    page.import_legacy_from(LEGACY_FIXTURE)
    assert len(s_shell.repo.habits()) == 8
    assert "Nothing new" in win_of(s_shell).toasts.text()
    assert len(list((s_shell.data_dir / "backups").glob("madhura-pre-import-*.db"))) == 1


def test_import_legacy_menu_lists_candidates(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qtbot) -> None:
    page = settings_page(s_shell)
    bad = tmp_path / "habits.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(legacy_import, "find_legacy_habits", lambda: [bad, LEGACY_FIXTURE])
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    menu = page.last_menu()
    assert menu is not None
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert len(texts) == 2 and "8 new habits" in texts[0] and texts[1] == "Choose file…"
    menu.actions()[0].trigger()
    menu.close()
    assert len(s_shell.repo.habits()) == 8


def test_import_legacy_rejects_bad_file(s_shell: AppContext, tmp_path: Path) -> None:
    page = settings_page(s_shell)
    bad = tmp_path / "habits.json"
    bad.write_text(json.dumps({"something": 1}), encoding="utf-8")
    assert page.import_legacy_from(bad) == 0
    assert win_of(s_shell).toasts.text() == "That file isn't an old Madhura habits file."
    assert s_shell.repo.habits() == []


def test_import_aborts_when_backup_fails(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    page = settings_page(s_shell)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(backup, "backup_now", broken)
    assert page.import_legacy_from(LEGACY_FIXTURE) == 0
    assert s_shell.repo.habits() == []
    assert "nothing was imported" in win_of(s_shell).toasts.text()


def test_restore_flow_cancel_and_restart(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    page = settings_page(s_shell)
    win = win_of(s_shell)
    made = backup.backup_now(s_shell.repo.conn, s_shell.data_dir / "backups", "2026-10-13")
    staged = s_shell.data_dir / "madhura.db.restore"

    qtbot.mouseClick(page.restore_button, Qt.MouseButton.LeftButton)
    menu = page.last_menu()
    assert menu is not None
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels[0] == backup_label(made) == "Daily backup · Tue 13 Oct"
    assert labels[-1] == "Choose file…"
    menu.close()

    # Cancel: the staged copy is removed again.
    page.restore_from(made)
    pump()
    sheet = win.sheet_host.current()
    assert isinstance(sheet, ConfirmSheet) and sheet.ok_button.text() == "Restart now"
    assert staged.is_file()
    sheet.reject()
    pump(10)
    assert not staged.exists() and not win.has_sheet()

    # Restart now: relaunch scheduled, then the app quits; the staged file stays for startup.
    scheduled: list[list[str]] = []
    cancelled: list[bool] = []
    monkeypatch.setattr(sp, "_schedule_relaunch", lambda argv: scheduled.append(argv) or (lambda: cancelled.append(True)))
    page.restore_from(made)
    pump()
    win.sheet_host.current().accept()
    pump(10)
    assert scheduled and scheduled[0][-1] != "--autostart"
    assert s_shell.flags["quit_calls"] == 1 and win.quitting
    assert cancelled == [] and staged.is_file()


def test_restore_rejects_non_backups(s_shell: AppContext, tmp_path: Path) -> None:
    page = settings_page(s_shell)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"definitely not sqlite")
    page.restore_from(junk)
    assert win_of(s_shell).toasts.text() == "That file isn't a Madhura backup."
    assert not win_of(s_shell).has_sheet()
    assert not (s_shell.data_dir / "madhura.db.restore").exists()


def test_restore_falls_back_to_file_picker(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    page = settings_page(s_shell)
    for old in (s_shell.data_dir / "backups").glob("*.db"):
        old.unlink()
    asked: list[str] = []
    monkeypatch.setattr(dialogs, "get_open_path", lambda _p, _t, _f="", directory="": asked.append(directory))
    page.restore_button.click()
    assert asked == [str(s_shell.data_dir / "backups")]


def test_schedule_relaunch_registers_and_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[object] = []
    monkeypatch.setattr(sp.atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(sp.atexit, "unregister", lambda fn: registered.remove(fn))
    cancel = sp._schedule_relaunch(["python", "-m", "madhura"])  # noqa: SLF001
    assert len(registered) == 1
    cancel()
    assert registered == []


# --------------------------------------------------------------------------- reset


def _seed(ctx: AppContext) -> None:
    repo = ctx.repo
    sid = repo.create_session("Old work", "Proj", None, datetime(2026, 10, 13, 9, 0), 1800)
    repo.update_session(sid, focused_seconds=1800, outcome="completed", ended_at=datetime(2026, 10, 13, 9, 30))
    repo.add_task("Next thing", 25, plan_date=date(2026, 10, 14))
    hid = repo.add_habit("Read", time="07:00")
    repo.set_habit_status(hid, date(2026, 10, 14), "done")
    repo.add_capture("stray thought")
    repo.set_day_note(date(2026, 10, 13), "good day")
    repo.kv_set("nudge_done_date", "2026-10-14")


def test_reset_all_requires_typing_reset_and_wipes_data(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch, qtbot) -> None:
    page = settings_page(s_shell)
    win = win_of(s_shell)
    _seed(s_shell)
    s_shell.engine.start("Running now", 30)
    resets: list[bool] = []
    if s_shell.habit_reminders is not None:
        monkeypatch.setattr(s_shell.habit_reminders, "reset", lambda: resets.append(True))
    kinds = record(s_shell.events.data_changed)

    qtbot.mouseClick(page.reset_button, Qt.MouseButton.LeftButton)
    pump()
    sheet = win.sheet_host.current()
    assert isinstance(sheet, ConfirmSheet)
    assert sheet.confirm_field is not None and not sheet.ok_button.isEnabled()
    sheet.confirm_field.set_text("rese")
    assert not sheet.ok_button.isEnabled()
    sheet.confirm_field.set_text("reset")
    assert sheet.ok_button.isEnabled()
    sheet.ok_button.click()
    pump(10)

    repo = s_shell.repo
    assert str(s_shell.engine.state) == "idle"
    assert repo.recent_sessions() == [] and repo.active_sessions() == []
    assert repo.tasks_for_date(date(2026, 10, 14)) == []
    assert repo.habits(include_archived=True) == [] and repo.open_captures() == []
    assert repo.get_day_note(date(2026, 10, 13)) is None
    assert repo.kv_get("nudge_done_date") is None
    assert "all" in kinds
    if s_shell.habit_reminders is not None:
        assert resets == [True]
    pre = list((s_shell.data_dir / "backups").glob("madhura-pre-reset-*.db"))
    assert len(pre) == 1
    # The backup holds the data from before the reset.
    conn = sqlite3.connect(pre[0])
    try:
        assert conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 1
    finally:
        conn.close()
    assert "All data was reset" in win.toasts.text()
    assert _get(s_shell, "onboarded") is True  # settings are kept


def test_reset_cancelled_keeps_everything(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    _seed(s_shell)
    page.request_reset()
    pump()
    win_of(s_shell).sheet_host.current().reject()
    pump(10)
    assert len(s_shell.repo.habits()) == 1


def test_reset_during_break_and_failed_backup(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    page = settings_page(s_shell)
    _seed(s_shell)
    s_shell.engine.start_break(5)
    assert page.reset_all_data() is True
    assert str(s_shell.engine.state) == "idle"

    _seed(s_shell)

    def broken(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(backup, "backup_now", broken)
    assert page.reset_all_data() is False
    assert len(s_shell.repo.habits()) == 1
    assert "nothing was deleted" in win_of(s_shell).toasts.text()


# =========================================================================== About


def test_about_group(s_shell: AppContext, qtbot) -> None:
    page = settings_page(s_shell)
    win = win_of(s_shell)
    assert page.about_header.version_label.text() == f"Version {__version__}"
    texts = " ".join(lbl.text() for lbl in page.groups["About"].findChildren(type(page.about_header.version_label).__mro__[1]))
    for needle in ("Universfield", "Pixabay", "Abhay Gaur", "MIT License"):
        assert needle in texts
    qtbot.mouseClick(page.shortcuts_button, Qt.MouseButton.LeftButton)
    pump()
    assert isinstance(win.sheet_host.current(), ShortcutsSheet)
    page.show_shortcuts()  # already open: no second sheet
    assert win.sheet_host.queued() == []


def test_page_survives_theme_switch_and_narrow_window(s_shell: AppContext) -> None:
    page = settings_page(s_shell)
    win = win_of(s_shell)
    for scheme in ("light", "dark"):
        s_shell.settings.update(theme=scheme)
        pump()
        assert theme.current().scheme == scheme
    win.resize(900, 700)
    pump()
    assert page.column.width() <= 720
    assert page.horizontalScrollBar().maximum() == 0
    QApplication.processEvents()


def test_headless_reset_uses_blocking_confirm(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(s_shell)
    page = sp.SettingsPage(s_shell)
    monkeypatch.setattr(s_shell, "window", None)
    asked: list[dict] = []

    def confirm(_parent, title, text, ok_text="OK", danger=False, **kw):
        asked.append({"title": title, "ok": ok_text, "danger": danger, **kw})
        return True

    monkeypatch.setattr(dialogs, "confirm", confirm)
    toasts = record(s_shell.events.toast)
    page.request_reset()
    assert asked == [{"title": "Reset all data?", "ok": "Reset everything", "danger": True, "type_to_confirm": "reset"}]
    assert s_shell.repo.habits() == []
    assert toasts and "All data was reset" in toasts[-1][0]
    page.deleteLater()


def test_import_picker_cancelled_does_nothing(s_shell: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    page = settings_page(s_shell)
    monkeypatch.setattr(legacy_import, "find_legacy_habits", lambda: [])
    monkeypatch.setattr(dialogs, "get_open_path", lambda *a, **k: None)
    page.import_button.click()
    assert s_shell.repo.habits() == []
    assert not list((s_shell.data_dir / "backups").glob("madhura-pre-import-*.db"))
