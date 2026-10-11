"""SettingsPage — grouped, instant-apply settings (spec §1.10).

Layout (max width 720)::

    Settings
    Changes save automatically.
    Focus ─ Check-ins ─ Breaks ─ Accountability ─ Mini timer ─ Sound ─ Appearance ─ System ─ Data ─ About

Every control writes straight through ``ctx.settings.update`` and follows external changes
(``settings.changed``) without echoing. No raw combo/spin boxes: Select, SegmentedControl, Stepper,
TimeField, Toggle, Slider, DayChips and the :class:`~madhura.ui.pages._settings_kit.AccentPicker`.
Launch at login reflects ``platform.autostart.status()`` (re-read in :meth:`SettingsPage.on_shown`).
Data actions back up first, never block on anything but file pickers, and confirm through sheets.
"""

from __future__ import annotations

import atexit
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from madhura import __version__
from madhura.data.settings import CHECKIN_INTERVALS
from madhura.platform import IS_MAC, IS_WINDOWS, autostart
from madhura.ui import dialogs, fonts, theme
from madhura.ui.pages._settings_kit import (
    NUDGE_EXPLANATIONS,
    AccentPicker,
    BackupFailed,
    backup_label,
    goal_label,
    hour_label,
    import_legacy,
    minutes_label,
    open_path,
    shorten_path,
    take_backup,
    time_example,
    work_hours_hint,
)
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.chips import DayChips, SegmentedControl
from madhura.ui.widgets.empty_state import IconMedallion
from madhura.ui.widgets.inputs import Slider, Stepper, TimeField
from madhura.ui.widgets.labels import Label
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.page import PageHeader, PageScroll
from madhura.ui.widgets.select import Select
from madhura.ui.widgets.settings_row import SettingsGroup, SettingsRow
from madhura.ui.widgets.sheet import ConfirmSheet
from madhura.ui.widgets.toggle import Toggle

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["SettingsPage", "MAX_WIDTH", "GROUP_TITLES", "RESET_PHRASE"]

log = logging.getLogger(__name__)

MAX_WIDTH = 720
RESET_PHRASE = "reset"
GROUP_TITLES: tuple[str, ...] = (
    "Focus",
    "Check-ins",
    "Breaks",
    "Accountability",
    "Mini timer",
    "Sound",
    "Appearance",
    "System",
    "Data",
    "About",
)

_TIMEOUT_OPTIONS = [(30, "30 seconds"), (60, "1 minute"), (120, "2 minutes")]
_INTERVAL_OPTIONS = [(m, f"Every {m} min") for m in CHECKIN_INTERVALS]
_SOUND_ROWS = (
    ("sound_checkin", "Check-ins", None),
    ("sound_complete", "Session complete", None),
    ("sound_break_over", "Break over", None),
    ("sound_habit", "Habit reminders", None),
    ("sound_nudge", "Nudges", "Gentle nudges stay silent unless this is on."),
)
_KEYBOARD_TEST_DELAY_MS = 450
#: Theme/accent re-style every widget (~0.5 s): arrowing across the options applies only where
#: the user stops (Enter/Space/focus-out apply at once).
_RESTYLE_KEY_COMMIT_MS = 250
_SELECT_MIN_WIDTH = 136
_ACTION_MIN_WIDTH = 128
_MENU_LIMIT = 8


def _documents_dir() -> Path:
    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    path = Path(loc) if loc else Path.home()
    return path if path.is_dir() else Path.home()


def _schedule_relaunch(argv: list[str]) -> Callable[[], None]:
    """Start *argv* detached once this process has fully exited (after the single-instance lock is
    released, so the new instance becomes primary). Returns a function that cancels it."""
    cwd = os.getcwd()

    def launch() -> None:
        try:
            from PySide6.QtCore import QProcess

            # The detached process inherits os.environ: a frozen build must start as a fresh instance.
            autostart.prepare_relaunch_environment(os.environ)
            result = QProcess.startDetached(argv[0], argv[1:], cwd)
            ok = result[0] if isinstance(result, tuple) else result
            if not ok:
                log.error("Could not relaunch Madhura: %s", argv)
        except Exception:  # noqa: BLE001 - we're exiting; never raise from atexit
            log.exception("Relaunch failed")

    atexit.register(launch)
    return lambda: atexit.unregister(launch)


class _GoalStepper(Stepper):
    """Daily-goal stepper (hours) whose width fits its widest value, so the − button never moves."""

    def setFixedWidth(self, w: int) -> None:  # noqa: N802 - Stepper resizes itself on every step
        widest = getattr(self, "_stable_width", None)
        super().setFixedWidth(widest if widest is not None else w)

    def stabilize(self, labels: list[str]) -> None:
        """Lock the width to fit every label in *labels*."""
        fm = self.fontMetrics()
        btn = self.height() - 8
        self._stable_width = 8 + 2 * btn + max(fm.horizontalAdvance(t) for t in labels) + 24
        super().setFixedWidth(self._stable_width)


class _AboutHeader(QWidget):
    """Logo, name, tagline and version at the top of the About card."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        self.medallion = IconMedallion("logo", 40, self)
        lay.addWidget(self.medallion, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        name = QLabel("Madhura", self)
        name.setFont(fonts.font(15, 600))
        col.addWidget(name)
        col.addWidget(Label("Plan the hour. Stay with it.", "caption", self))
        lay.addLayout(col, 1)
        self.version_label = Label(f"Version {__version__}", "caption", self)
        self.version_label.setFont(fonts.font(12, 400, tabular=True))
        lay.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignVCenter)
        theme.current().changed.connect(self.medallion.update)


class SettingsPage(PageScroll):
    """Settings page (max width 720): grouped cards of instant-apply rows.

    Public handles for tests/integration: :attr:`groups` (title → SettingsGroup), :attr:`rows`
    and :attr:`controls` (setting key → SettingsRow / control widget), plus the named action
    buttons. :meth:`on_shown` re-reads launch-at-login status.
    """

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(MAX_WIDTH, parent, spacing=0)
        self._ctx = ctx
        self.groups: dict[str, SettingsGroup] = {}
        self.rows: dict[str, SettingsRow] = {}
        self.controls: dict[str, QWidget] = {}
        self._syncers: dict[str, Callable[[], None]] = {}
        self._menu: Menu | None = None
        self._staged_restore: Path | None = None

        self.header = PageHeader("Settings", parent=self.column, subtitle="Changes save automatically.")
        self.add(self.header)
        self.content_layout.addSpacing(24)

        self._build_focus()
        self._build_checkins()
        self._build_breaks()
        self._build_accountability()
        self._build_mini_timer()
        self._build_sound()
        self._build_appearance()
        self._build_system()
        self._build_data()
        self._build_about()

        self._update_dependents()
        self._refresh_autostart()
        ctx.settings.changed.connect(self._on_setting_changed)

    # ================================================================== plumbing

    def _get(self, key: str) -> Any:
        return self._ctx.settings.get(key)

    def _set(self, key: str, value: Any) -> None:
        """Instant-apply one setting; on rejection the control snaps back to the stored value."""
        try:
            self._ctx.settings.update(**{key: value})
        except (KeyError, ValueError):
            log.exception("Rejected setting %s=%r", key, value)
            sync = self._syncers.get(key)
            if sync is not None:
                sync()

    def _group(self, title: str, description: str | None = None) -> SettingsGroup:
        if self.groups:
            self.content_layout.addSpacing(32)
        group = SettingsGroup(title, description, self.column)
        self.groups[title] = group
        self.add(group)
        return group

    def _row(self, group: SettingsGroup, key: str, title: str, description: str | None, control: QWidget) -> SettingsRow:
        row = group.add(title, description, control)
        self.rows[key] = row
        self.controls[key] = control
        return row

    def _toggle(self, group: SettingsGroup, key: str, title: str, description: str | None = None) -> SettingsRow:
        toggle = Toggle(bool(self._get(key)))
        toggle.clicked.connect(lambda _c=False, k=key, w=toggle: self._set(k, w.isChecked()))
        self._syncers[key] = lambda k=key, w=toggle: w.setChecked(bool(self._get(k)))
        return self._row(group, key, title, description, toggle)

    def _select(
        self, group: SettingsGroup, key: str, title: str, description: str | None, options: list[tuple[Any, str]]
    ) -> SettingsRow:
        select = Select(options, self._get(key), min_width=_SELECT_MIN_WIDTH)
        select.valueChanged.connect(lambda v, k=key: self._set(k, v))
        self._syncers[key] = lambda k=key, w=select: w.set_value(self._get(k))
        return self._row(group, key, title, description, select)

    def _segmented(
        self, group: SettingsGroup, key: str, title: str, description: str | None, options: list[tuple[Any, str]]
    ) -> SettingsRow:
        seg = SegmentedControl(options, self._get(key), size="sm")
        seg.valueChanged.connect(lambda v, k=key: self._set(k, v))
        self._syncers[key] = lambda k=key, w=seg: w.set_value(self._get(k))
        return self._row(group, key, title, description, seg)

    def _int_stepper(
        self, group: SettingsGroup, key: str, title: str, description: str | None, lo: int, hi: int, suffix: str
    ) -> SettingsRow:
        stepper = Stepper(lo, hi, 1, int(self._get(key)), suffix)
        stepper.valueChanged.connect(lambda v, k=key: self._set(k, int(round(v))))
        self._syncers[key] = lambda k=key, w=stepper: w.set_value(int(self._get(k)))
        return self._row(group, key, title, description, stepper)

    def _toast(
        self, text: str, kind: str = "info", action_text: str | None = None, on_action: Callable[[], object] | None = None
    ) -> None:
        window = self._ctx.window
        if window is not None:
            window.show_toast(text, kind, action_text, on_action)
        else:
            self._ctx.events.toast.emit(text, kind)

    def _popup(self, menu: Menu, anchor: QWidget) -> None:
        self._menu = menu
        menu.aboutToHide.connect(menu.deleteLater)  # one menu per click; don't let them pile up
        menu.popup_below(anchor, align="right")

    def last_menu(self) -> Menu | None:
        """The most recent action menu (Import / Restore) — for tests."""
        return self._menu

    # ================================================================== Focus

    def _duration_options(self) -> list[tuple[int, str]]:
        values = sorted(set(self._get("duration_presets")) | {int(self._get("default_minutes"))})
        return [(m, minutes_label(m)) for m in values]

    def _hour_options(self) -> list[tuple[int, str]]:
        use_24h = self._ctx.use_24h()
        return [(h, hour_label(h, use_24h)) for h in range(7)]

    def _build_focus(self) -> None:
        g = self._group("Focus")
        self._select(g, "default_minutes", "Default duration", "Preselected when you plan a block.", self._duration_options())

        def sync_durations() -> None:
            # A custom default (e.g. 35) that isn't a preset still shows as its own option.
            self.controls["default_minutes"].set_options(  # type: ignore[attr-defined]
                self._duration_options(), self._get("default_minutes")
            )

        self._syncers["default_minutes"] = self._syncers["duration_presets"] = sync_durations

        goal = _GoalStepper(0.5, 12, 0.5, self._get("daily_goal_minutes") / 60.0, fmt=goal_label)
        goal.stabilize([goal_label(h / 2) for h in range(1, 25)])
        goal.valueChanged.connect(lambda v: self._set("daily_goal_minutes", int(round(v * 60))))
        self._syncers["daily_goal_minutes"] = lambda: goal.set_value(self._get("daily_goal_minutes") / 60.0)
        self._row(g, "daily_goal_minutes", "Daily goal", "Focused time to aim for each day.", goal)

        self._segmented(
            g,
            "after_start",
            "After Lock in",
            "Get out of the way, or keep the timer in view.",
            [("mini", "Hide to mini timer"), ("stay", "Stay open")],
        )
        self._select(
            g,
            "day_start_hour",
            "Day starts at",
            "Late-night sessions before this count toward the previous day.",
            self._hour_options(),
        )

    # ================================================================== Check-ins

    def _build_checkins(self) -> None:
        g = self._group("Check-ins")
        self._toggle(g, "checkin_enabled", "Ask during sessions", "A quiet “Still on it?” while you focus. Only you see the answers.")
        self._select(g, "checkin_interval_minutes", "Interval", "Counted in focused minutes.", _INTERVAL_OPTIONS)
        self._toggle(g, "checkin_adaptive", "Adaptive spacing", "Space them out when you're consistently focused.")
        self._select(
            g,
            "checkin_timeout_seconds",
            "Auto-dismiss after",
            "Unanswered check-ins count as missed — never as focused.",
            _TIMEOUT_OPTIONS,
        )

    # ================================================================== Breaks

    def _build_breaks(self) -> None:
        g = self._group("Breaks")
        self._toggle(g, "breaks_enabled", "Offer breaks", "A short break when a session ends.")
        self._int_stepper(g, "break_minutes", "Break length", None, 1, 30, " min")
        self._toggle(g, "auto_start_break", "Start breaks automatically", "Begin the break as soon as a session completes.")

    # ================================================================== Accountability

    def _build_accountability(self) -> None:
        g = self._group("Accountability", "Between blocks, during work hours, Madhura can nudge you to plan the next one.")
        self.nudge_row = self._segmented(
            g,
            "nudge_mode",
            "Nudges",
            NUDGE_EXPLANATIONS.get(self._get("nudge_mode"), ""),
            [("off", "Off"), ("gentle", "Gentle"), ("firm", "Firm")],
        )
        self._int_stepper(g, "nudge_idle_minutes", "Nudge after", "Minutes between blocks before the first nudge.", 1, 60, " min")

        days = DayChips(int(self._get("work_days")))
        days.maskChanged.connect(lambda m: self._set("work_days", int(m)))
        self._syncers["work_days"] = lambda: days.set_mask(int(self._get("work_days")))
        self._row(g, "work_days", "Work days", "Nudges, streaks and your weekly goal follow these days.", days)

        use_24h = self._ctx.use_24h()
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.work_start = TimeField(self._get("work_start"), box, use_24h=use_24h)
        self.work_end = TimeField(self._get("work_end"), box, use_24h=use_24h)
        self.work_start.time_edit.setAccessibleName("Work hours start")
        self.work_end.time_edit.setAccessibleName("Work hours end")
        lay.addWidget(self.work_start)
        lay.addWidget(Label("to", "caption", box))
        lay.addWidget(self.work_end)
        box.setAccessibleName("Work hours")
        self.work_start.timeChanged.connect(lambda t: self._set("work_start", t))
        self.work_end.timeChanged.connect(lambda t: self._set("work_end", t))
        self._syncers["work_start"] = lambda: self.work_start.set_time_str(self._get("work_start"))
        self._syncers["work_end"] = lambda: self.work_end.set_time_str(self._get("work_end"))
        self.work_hours_row = g.add("Work hours", work_hours_hint(self._get("work_start"), self._get("work_end")), box)
        self.rows["work_start"] = self.rows["work_end"] = self.work_hours_row
        self.controls["work_start"] = self.work_start
        self.controls["work_end"] = self.work_end

    # ================================================================== Mini timer

    def _build_mini_timer(self) -> None:
        g = self._group("Mini timer")
        self._segmented(
            g,
            "pill_mode",
            "Show mini timer",
            "A small floating timer while a session runs.",
            [("hidden", "When window hidden"), ("always", "Always"), ("never", "Never")],
        )
        self._toggle(g, "pill_show_seconds", "Show seconds", "Otherwise it counts whole minutes until the last one.")

    # ================================================================== Sound

    def _build_sound(self) -> None:
        g = self._group("Sound")
        self.volume_slider = Slider(0, 100, int(self._get("sound_volume")))
        self.volume_slider.setFixedWidth(168)
        self.volume_slider.setAccessibleName("Volume")
        self.volume_label = Label("", "caption")
        self.volume_label.setFont(fonts.font(12, 500, tabular=True))
        self.volume_label.setFixedWidth(self.volume_label.fontMetrics().horizontalAdvance("100%") + 4)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.test_sound_button = Button("Test", "secondary", icon="volume")
        self.test_sound_button.setAccessibleName("Play test sound")
        self._test_timer = QTimer(self)
        self._test_timer.setSingleShot(True)
        self._test_timer.setInterval(_KEYBOARD_TEST_DELAY_MS)
        self._test_timer.timeout.connect(self.play_test_sound)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.valueReleased.connect(self._on_volume_released)
        self.test_sound_button.clicked.connect(self.play_test_sound)

        def sync_volume() -> None:
            v = int(self._get("sound_volume"))
            if self.volume_slider.value() != v:
                self.volume_slider.blockSignals(True)
                self.volume_slider.setValue(v)
                self.volume_slider.blockSignals(False)
            self._update_volume_label(v)

        self._syncers["sound_volume"] = sync_volume
        row = self._row(g, "sound_volume", "Volume", "Let go of the slider to hear it.", self.volume_slider)
        row.add_control(self.volume_label)
        row.add_control(self.test_sound_button)
        self._update_volume_label(int(self._get("sound_volume")))
        for key, title, desc in _SOUND_ROWS:
            self._toggle(g, key, title, desc)

    def _update_volume_label(self, value: int) -> None:
        self.volume_label.setText("Off" if value <= 0 else f"{value}%")
        self.volume_slider.setAccessibleDescription(self.volume_label.text())

    def _on_volume_changed(self, value: int) -> None:
        self._update_volume_label(value)
        self._set("sound_volume", int(value))
        if not self.volume_slider.isSliderDown():
            # Keyboard / wheel: play once the value settles (mouse plays on release instead).
            self._test_timer.start()

    def _on_volume_released(self, value: int) -> None:
        self._test_timer.stop()
        self._set("sound_volume", int(value))
        self.play_test_sound()

    def play_test_sound(self) -> None:
        """Play the chime at the current volume (explains when the volume is off)."""
        self._test_timer.stop()
        if int(self._get("sound_volume")) <= 0:
            self._toast("Volume is off — slide it up to hear Madhura.")
            return
        self._ctx.sound.play("test")

    # ================================================================== Appearance

    def _build_appearance(self) -> None:
        g = self._group("Appearance")
        self._segmented(g, "theme", "Theme", None, [("system", "System"), ("dark", "Dark"), ("light", "Light")])
        self.controls["theme"].set_key_commit_delay(_RESTYLE_KEY_COMMIT_MS)
        self.accent_picker = AccentPicker(self._get("accent"))
        self.accent_picker.set_key_commit_delay(_RESTYLE_KEY_COMMIT_MS)
        self.accent_picker.valueChanged.connect(lambda name: self._set("accent", name))
        self._syncers["accent"] = lambda: self.accent_picker.set_value(self._get("accent"))
        self._row(g, "accent", "Accent color", None, self.accent_picker)
        self._toggle(g, "reduce_motion", "Reduce motion", "Skip animations and keep transitions instant.")
        self._segmented(
            g,
            "time_format",
            "Time format",
            time_example(self._ctx.use_24h()),
            [("system", "System"), ("24h", "24-hour"), ("12h", "12-hour")],
        )

    # ================================================================== System

    def _build_system(self) -> None:
        g = self._group("System")
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.repair_button = Button("Repair", "secondary", size="sm", parent=box)
        self.repair_button.clicked.connect(self.repair_autostart)
        self.autostart_toggle = Toggle(False, box, accessible_name="Launch at login")
        self.autostart_toggle.clicked.connect(lambda _c=False: self._on_autostart_clicked(self.autostart_toggle.isChecked()))
        lay.addWidget(self.repair_button, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.autostart_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        self.autostart_row = g.add("Launch at login", None, box)
        self.rows["launch_at_login"] = self.autostart_row
        self.controls["launch_at_login"] = self.autostart_toggle

        tray = self._ctx.tray_available
        self._toggle(
            g,
            "close_to_tray",
            "Keep running in the tray",
            "Closing the window hides Madhura instead of quitting."
            if tray
            else "No system tray was found, so closing the window quits Madhura.",
        )
        self.controls["close_to_tray"].setEnabled(tray)
        self._toggle(
            g,
            "start_minimized",
            "Start minimized",
            "Open quietly in the tray without showing the window." if tray else "Open minimized to the taskbar.",
        )

    def _set_desc_tone(self, row: SettingsRow, tone: str | None) -> None:
        theme.set_prop(row.description_label, "tone", tone or "")

    def _refresh_autostart(self) -> None:
        """Mirror ``autostart.status()`` in the toggle, description and Repair button."""
        supported = autostart.is_supported()
        state = autostart.status() if supported else "off"
        toggle = self.autostart_toggle
        toggle.setEnabled(supported)
        toggle.setChecked(supported and state in ("on", "needs_repair"))
        self.repair_button.setVisible(supported and state == "needs_repair")
        tone: str | None = None
        if not supported:
            text = (
                "Not available on macOS yet — add Madhura in System Settings › General › Login Items."
                if IS_MAC
                else "Not available on this system."
            )
        elif state == "disabled_by_os":
            text = (
                "Disabled in Windows startup settings. Turn it on to allow it again."
                if IS_WINDOWS
                else "Disabled in your desktop's startup settings. Turn it on to allow it again."
            )
            tone = "warning"
        elif state == "needs_repair":
            text = "Madhura moved since this was set up, so it may not start. Repair it to fix the link."
            tone = "warning"
        else:
            text = "Start quietly in the tray when you sign in."
        self.autostart_row.set_description(text)
        self._set_desc_tone(self.autostart_row, tone)
        self._autostart_state = state if supported else "unsupported"

    def autostart_state(self) -> str:
        """Last status shown ("on", "off", "disabled_by_os", "needs_repair" or "unsupported")."""
        return self._autostart_state

    def _on_autostart_clicked(self, enabled: bool) -> None:
        if not autostart.set_enabled(enabled):
            self._toast("Couldn't change launch at login.", "error")
        self._refresh_autostart()

    def repair_autostart(self) -> None:
        """Rewrite the launch-at-login entry for this install."""
        if autostart.set_enabled(True):
            self._toast("Launch at login repaired.", "success")
        else:
            self._toast("Couldn't repair launch at login.", "error")
        self._refresh_autostart()

    # ================================================================== Data

    def _build_data(self) -> None:
        g = self._group("Data", "Everything stays on this computer.")
        self.open_data_button = Button("Open folder", "secondary", icon="folder")
        self.open_data_button.setMinimumWidth(_ACTION_MIN_WIDTH)
        self.open_data_button.clicked.connect(self.open_data_folder)
        self.data_folder_row = g.add("Data folder", shorten_path(self._ctx.data_dir), self.open_data_button)
        self.data_folder_row.description_label.setToolTip(str(self._ctx.data_dir))
        self.data_folder_row.description_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.export_button = Button("Export…", "secondary", icon="download")
        self.export_button.setMinimumWidth(_ACTION_MIN_WIDTH)
        self.export_button.clicked.connect(self.export_everything)
        g.add("Export everything", "Sessions, tasks, habits and notes as one JSON file.", self.export_button)

        self.import_button = Button("Import…", "secondary", icon="upload")
        self.import_button.setMinimumWidth(_ACTION_MIN_WIDTH)
        self.import_button.clicked.connect(self._on_import_clicked)
        g.add("Import old Madhura habits", "From the original app's habits.json. Duplicates are skipped.", self.import_button)

        self.restore_button = Button("Restore…", "secondary", icon="undo")
        self.restore_button.setMinimumWidth(_ACTION_MIN_WIDTH)
        self.restore_button.clicked.connect(self._on_restore_clicked)
        g.add("Restore from backup", "Daily backups are kept for a week. Madhura restarts to restore.", self.restore_button)

        self.reset_button = Button("Reset…", "danger", icon="trash")
        self.reset_button.setMinimumWidth(_ACTION_MIN_WIDTH)
        self.reset_button.clicked.connect(self.request_reset)
        g.add("Reset all data", "Delete every session, task and habit. A backup is saved first.", self.reset_button)

    def open_data_folder(self) -> None:
        """Show the data folder in the file manager."""
        if not open_path(self._ctx.data_dir):
            self._toast("Couldn't open the data folder.", "error")

    # ------------------------------------------------------------------ export
    def export_everything(self) -> None:
        """Ask for a file and write every table as JSON (``data.export.export_all_json``)."""
        default = _documents_dir() / f"madhura-export-{self._ctx.today().isoformat()}.json"
        chosen = dialogs.get_save_path(self, "Export everything", str(default), "JSON (*.json)")
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".json":
            path = path.with_name(path.name + ".json")
        try:
            from madhura.data import export

            export.export_all_json(path, self._ctx.repo)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            log.exception("Export failed")
            reason = getattr(exc, "strerror", None) or "the file couldn't be written"
            self._toast(f"Couldn't export — {reason}.", "error")
            return
        self._toast(f"Exported to {path.name}", "success", "Show", lambda: open_path(path.parent))

    # ------------------------------------------------------------------ legacy import
    def _on_import_clicked(self) -> None:
        from madhura.data import legacy_import

        found: list[tuple[Path, int]] = []
        for p in legacy_import.find_legacy_habits():
            try:
                found.append((p, len(legacy_import.pending_legacy_habits(p, self._ctx.repo))))
            except legacy_import.LegacyImportError:
                continue
        if not found:
            self.choose_legacy_file()
            return
        menu = Menu(self)
        for p, n in found[:_MENU_LIMIT]:
            detail = f"{n} new habit{'s' if n != 1 else ''}" if n else "nothing new"
            menu.add_action(f"{shorten_path(p, 44)}  ·  {detail}", lambda p=p: self.import_legacy_from(p), icon="inbox")
        menu.add_separator()
        menu.add_action("Choose file…", self.choose_legacy_file, icon="folder")
        self._popup(menu, self.import_button)

    def choose_legacy_file(self) -> None:
        """Pick a habits.json with the file dialog and import it."""
        chosen = dialogs.get_open_path(
            self, "Import old Madhura habits", "Madhura habits (*.json);;All files (*)", str(Path.home())
        )
        if chosen:
            self.import_legacy_from(chosen)

    def import_legacy_from(self, path: str | os.PathLike[str]) -> int:
        """Back up, import the habits in *path* and report with a toast. Returns how many were added."""
        from madhura.data import legacy_import

        try:
            added = import_legacy(self._ctx, path)
        except legacy_import.LegacyImportError:
            self._toast("That file isn't an old Madhura habits file.", "error")
            return 0
        except BackupFailed:
            self._toast("Couldn't save a backup first, so nothing was imported.", "error")
            return 0
        except sqlite3.Error:
            log.exception("Legacy import failed")
            self._toast("Couldn't import those habits.", "error")
            return 0
        if added == 0:
            self._toast("Nothing new to import — those habits are already here.")
        else:
            self._toast(
                f"Imported {added} habit{'s' if added != 1 else ''}",
                "success",
                "View",
                lambda: self._ctx.events.navigate.emit("habits"),
            )
        return added

    # ------------------------------------------------------------------ restore
    def _backups_dir(self) -> Path:
        return self._ctx.data_dir / "backups"

    def _on_restore_clicked(self) -> None:
        from madhura.data import backup

        try:
            items = backup.list_backups(self._backups_dir())
        except OSError:
            items = []
        if not items:
            self.choose_backup_file()
            return
        menu = Menu(self)
        for p in items[:_MENU_LIMIT]:
            menu.add_action(backup_label(p), lambda p=p: self.restore_from(p), icon="clock")
        menu.add_separator()
        menu.add_action("Choose file…", self.choose_backup_file, icon="folder")
        self._popup(menu, self.restore_button)

    def choose_backup_file(self) -> None:
        """Pick a backup file with the file dialog."""
        chosen = dialogs.get_open_path(
            self, "Restore from backup", "Madhura backups (*.db);;All files (*)", str(self._backups_dir())
        )
        if chosen:
            self.restore_from(chosen)

    def restore_from(self, path: str | os.PathLike[str]) -> None:
        """Stage *path* (validated) as ``madhura.db.restore`` and offer to restart now."""
        from madhura.data import backup

        source = Path(path)
        try:
            staged = backup.stage_restore(source, self._ctx.data_dir / "madhura.db")
        except (ValueError, OSError):
            log.warning("Not a usable backup: %s", source, exc_info=True)
            self._toast("That file isn't a Madhura backup.", "error")
            return
        self._staged_restore = staged
        sheet = ConfirmSheet(
            "Restore this backup?",
            f"Madhura will restart and load “{backup_label(source)}”. Your current data is kept as a "
            "backup too, so you can switch back.",
            "Restart now",
        )
        sheet.accepted.connect(self._restart_to_restore)
        sheet.rejected.connect(self._cancel_restore)
        self._show_sheet(sheet)

    def _cancel_restore(self) -> None:
        staged, self._staged_restore = self._staged_restore, None
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove the staged restore %s", staged)

    def _restart_to_restore(self) -> None:
        self._staged_restore = None
        cancel = _schedule_relaunch(autostart.relaunch_command())
        window = self._ctx.window
        if window is not None:
            window.quit_app()
        if not (self._ctx.flags.get("quitting") or getattr(window, "quitting", False)):
            cancel()
            self._toast("The backup will be restored the next time Madhura starts.")

    # ------------------------------------------------------------------ reset
    def request_reset(self) -> None:
        """Ask (type “reset” to confirm), then :meth:`reset_all_data`."""
        sheet = ConfirmSheet(
            "Reset all data?",
            "Every session, task, habit and note will be deleted. Madhura saves a backup first, and "
            "your settings stay as they are.",
            "Reset everything",
            danger=True,
            type_to_confirm=RESET_PHRASE,
        )
        sheet.accepted.connect(self.reset_all_data)
        self._show_sheet(sheet, danger=True, phrase=RESET_PHRASE)

    def reset_all_data(self) -> bool:
        """End any session, back up, wipe the database, reset services, announce ``data_changed('all')``."""
        ctx = self._ctx
        engine = ctx.engine
        if engine.is_active:
            engine.abandon()
        elif str(engine.state) == "break":
            engine.skip_break()
        try:
            take_backup(ctx, "pre-reset")
        except BackupFailed:
            self._toast("Couldn't save a backup first, so nothing was deleted.", "error")
            return False
        try:
            ctx.repo.reset_all()
        except sqlite3.Error:
            log.exception("Reset failed")
            self._toast("Couldn't reset your data.", "error")
            return False
        for service, method in ((ctx.habit_reminders, "reset"), (ctx.nudges, "resume")):
            fn = getattr(service, method, None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001 - a feature service must not block the reset
                    log.exception("%s.%s() after reset failed", type(service).__name__, method)
        ctx.attention.clear_quiet()
        ctx.events.data_changed.emit("all")
        self._toast("All data was reset. A backup was saved first.", "success")
        return True

    def _show_sheet(self, sheet: ConfirmSheet, *, danger: bool = False, phrase: str | None = None) -> None:
        window = self._ctx.window
        if window is not None:
            window.show_sheet(sheet)
            return
        # No main window (headless use): fall back to the blocking dialog with the same wording.
        ok = dialogs.confirm(
            None, sheet.title_text(), sheet.text_label.text(), sheet.ok_button.text(), danger, type_to_confirm=phrase
        )
        if ok:
            sheet.accept()
        else:
            sheet.reject()

    # ================================================================== About

    def _build_about(self) -> None:
        g = self._group("About")
        self.about_header = _AboutHeader(g.card)
        g.add_widget(self.about_header)
        self.shortcuts_button = Button("View shortcuts", "secondary", icon="keyboard")
        self.shortcuts_button.clicked.connect(self.show_shortcuts)
        g.add("Keyboard shortcuts", "Press ? anywhere in Madhura.", self.shortcuts_button)
        g.add("Credits", "Original Madhura by Abhay Gaur.\nNotification sound by Universfield via Pixabay.")
        g.add("License", "Free and open-source software.", Label("MIT License", "caption"))

    def show_shortcuts(self) -> None:
        """Open the keyboard shortcuts sheet."""
        window = self._ctx.window
        if window is None:
            return
        opener = getattr(window, "show_shortcuts", None)
        if callable(opener):
            opener()
            return
        from madhura.ui.shortcuts_sheet import ShortcutsSheet

        window.show_sheet(ShortcutsSheet())

    # ================================================================== reactions

    def _update_dependents(self) -> None:
        checkins = bool(self._get("checkin_enabled"))
        for key in ("checkin_interval_minutes", "checkin_adaptive", "checkin_timeout_seconds"):
            self.rows[key].setEnabled(checkins)
        breaks = bool(self._get("breaks_enabled"))
        for key in ("break_minutes", "auto_start_break"):
            self.rows[key].setEnabled(breaks)
        mode = self._get("nudge_mode")
        self.rows["nudge_idle_minutes"].setEnabled(mode != "off")
        self.nudge_row.set_description(NUDGE_EXPLANATIONS.get(mode, ""))
        self.work_hours_row.set_description(work_hours_hint(self._get("work_start"), self._get("work_end")))

    def _on_time_format(self) -> None:
        use_24h = self._ctx.use_24h()
        self.work_start.set_use_24h(use_24h)
        self.work_end.set_use_24h(use_24h)
        self.controls["day_start_hour"].set_options(self._hour_options(), self._get("day_start_hour"))  # type: ignore[attr-defined]
        self.rows["time_format"].set_description(time_example(use_24h))

    def _on_setting_changed(self, key: str) -> None:
        sync = self._syncers.get(key)
        if sync is not None:
            sync()
        if key == "time_format":
            self._on_time_format()
        if key in ("checkin_enabled", "breaks_enabled", "nudge_mode", "work_start", "work_end"):
            self._update_dependents()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isVisible() and self.isActiveWindow():
            # The user may have flipped the OS startup switch while Madhura was in the background.
            self._refresh_autostart()

    def on_shown(self) -> None:
        """Called by the shell when the page becomes visible: re-check launch at login (the user
        may have changed it in the OS) and re-sync every control."""
        for sync in self._syncers.values():
            sync()
        self._update_dependents()
        self._refresh_autostart()
