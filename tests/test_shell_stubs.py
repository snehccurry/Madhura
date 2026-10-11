"""Public API surface of the feature modules the shell wires together (spec §2, §5.3).

These checks are implementation-agnostic: they hold for the shell's stubs and must keep holding
once the feature agents replace them.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import date, datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from madhura.context import AppContext
from madhura.ui.widgets.popup import FloatingPanel
from madhura.ui.widgets.sheet import SheetContent
from test_shell_support import shell  # noqa: F401

MODULE_NAMES = {
    "madhura.core.habits": ["is_scheduled", "habit_streak", "last_7", "due_status", "minutes_late", "group_of"],
    "madhura.core.analytics": [
        "daily_totals",
        "weekly_totals",
        "hour_weekday_matrix",
        "focus_score",
        "came_back",
        "top_projects",
        "top_tasks",
        "drift_reasons",
        "period_delta",
        "goal_dates",
        "habit_completion",
        "best_day",
    ],
    "madhura.data.export": ["export_sessions_csv", "export_all_json"],
    "madhura.data.legacy_import": ["find_legacy_habits", "import_legacy_habits"],
    "madhura.platform.autostart": ["is_supported", "is_enabled", "status", "set_enabled", "launch_command"],
    "madhura.services.checkin_service": ["CheckInService"],
    "madhura.services.nudge_service": ["NudgeService"],
    "madhura.services.away_service": ["AwayService"],
    "madhura.services.habit_reminders": ["HabitReminderService"],
    "madhura.ui.widgets.charts": ["BarChart", "Heatmap", "HBarList"],
    "madhura.ui.widgets.stat_card": ["StatCard"],
    "madhura.ui.widgets.recap": ["WeeklyRecapCard"],
    "madhura.ui.pages.focus_page": ["FocusPage"],
    "madhura.ui.pages.focus.idle_view": ["PlanCard", "UpNextCard", "TodayCard", "ParkingLotCard", "IdleView"],
    "madhura.ui.pages.focus.active_view": ["ActiveView"],
    "madhura.ui.pages.habits_page": ["HabitsPage"],
    "madhura.ui.pages.habit_editor": ["HabitEditorSheet"],
    "madhura.ui.pages.insights_page": ["InsightsPage"],
    "madhura.ui.pages.settings_page": ["SettingsPage"],
    "madhura.ui.overlays.pill": ["FocusPill", "PillController"],
    "madhura.ui.overlays.checkin_popup": ["CheckInPopup"],
    "madhura.ui.overlays.nudge_popup": ["NudgePopup"],
    "madhura.ui.overlays.away_panel": ["AwayPanel"],
    "madhura.ui.overlays.habit_reminder_popup": ["HabitReminderPopup"],
    "madhura.ui.overlays.session_complete": [
        "present_session_complete",
        "show_pending_complete",
        "SessionCompleteSheet",
        "CompletionPanel",
    ],
    "madhura.ui.overlays.wrap_up": ["WrapUpSheet"],
    "madhura.ui.overlays.onboarding": ["OnboardingSheet"],
}


@pytest.mark.parametrize("module_name", sorted(MODULE_NAMES))
def test_module_exports(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    for name in MODULE_NAMES[module_name]:
        assert hasattr(mod, name), f"{module_name}.{name}"


def test_panels_and_sheets_have_the_right_bases() -> None:
    from madhura.ui.overlays import away_panel, checkin_popup, habit_reminder_popup, nudge_popup, onboarding, pill, session_complete, wrap_up
    from madhura.ui.pages import habit_editor

    for cls in (
        pill.FocusPill,
        checkin_popup.CheckInPopup,
        nudge_popup.NudgePopup,
        away_panel.AwayPanel,
        habit_reminder_popup.HabitReminderPopup,
        session_complete.CompletionPanel,
    ):
        assert issubclass(cls, FloatingPanel), cls
    for cls in (session_complete.SessionCompleteSheet, wrap_up.WrapUpSheet, onboarding.OnboardingSheet, habit_editor.HabitEditorSheet):
        assert issubclass(cls, SheetContent), cls
    assert onboarding.OnboardingSheet.dismissible is False


def test_service_apis(shell: AppContext) -> None:  # noqa: F811
    ck = shell.checkins
    assert ck is not None
    for sig in ("prompt_inline", "prompt_closed", "answered"):
        assert hasattr(ck, sig)
    assert set(ck.counts()) >= {"focused", "drifted", "missed"}
    assert ck.seconds_until_next() is None  # not focusing
    assert ck.timeout_progress() is None  # no prompt open

    nudges = shell.nudges
    assert nudges is not None and hasattr(nudges, "state_changed")
    label, cb = nudges.primary_action()
    assert isinstance(label, str) and label and callable(cb)
    nudges.snooze(15)
    assert isinstance(nudges.snoozed_until, datetime)
    nudges.resume()
    assert nudges.snoozed_until is None
    nudges.done_for_today()
    assert nudges.is_done_for_today is True
    nudges.resume()
    assert nudges.is_done_for_today is False
    assert isinstance(nudges.banner_visible, bool)

    pill = shell.pill
    assert pill is not None and hasattr(pill, "visibility_changed")
    assert isinstance(pill.is_showing(), bool)
    pill.toggle_user_visible()
    pill.pulse()

    hr = shell.habit_reminders
    assert hr is not None
    hr.reset()
    assert isinstance(hr.queued_count, int)
    assert shell.away is not None


def test_present_session_complete_paths(shell: AppContext) -> None:  # noqa: F811
    from madhura.ui.overlays.session_complete import present_session_complete, show_pending_complete

    shell.window.show_main()  # type: ignore[union-attr]
    sid = shell.repo.create_session("Write", None, None, datetime(2026, 10, 14, 9, 0), 1500)
    shell.repo.update_session(sid, focused_seconds=1500, outcome="completed", ended_at=datetime(2026, 10, 14, 9, 25))
    present_session_complete(shell, sid, "completed", True)  # restored → toast
    assert "closed" in shell.window.toasts.text()  # type: ignore[union-attr]
    present_session_complete(shell, 99999, "abandoned", False)  # row deleted
    assert shell.window.toasts.text() == "Too short to save"  # type: ignore[union-attr]
    present_session_complete(shell, sid, "completed", False)
    show_pending_complete(shell)


def test_sheets_construct_and_act(shell: AppContext) -> None:  # noqa: F811
    from madhura.ui.overlays.onboarding import OnboardingSheet
    from madhura.ui.overlays.session_complete import SessionCompleteSheet
    from madhura.ui.overlays.wrap_up import WrapUpSheet
    from madhura.ui.pages.habit_editor import HabitEditorSheet

    win = shell.window
    win.show_main()  # type: ignore[union-attr]
    sid = shell.repo.create_session("Write", None, None, datetime(2026, 10, 14, 9, 0), 1500)
    for sheet in (OnboardingSheet(shell), WrapUpSheet(shell), HabitEditorSheet(shell), SessionCompleteSheet(shell, sid, "completed")):
        win.show_sheet(sheet)  # type: ignore[union-attr]
        assert win.has_sheet()  # type: ignore[union-attr]
        win.close_sheet()  # type: ignore[union-attr]
        QApplication.processEvents()
    assert not win.has_sheet()  # type: ignore[union-attr]


def test_widgets_construct(shell: AppContext, qapp: QApplication) -> None:  # noqa: F811
    from madhura.ui.overlays.pill import FocusPill
    from madhura.ui.pages.focus.active_view import ActiveView
    from madhura.ui.pages.focus.idle_view import IdleView
    from madhura.ui.widgets.charts import BarChart, HBarList, Heatmap
    from madhura.ui.widgets.recap import WeeklyRecapCard
    from madhura.ui.widgets.stat_card import StatCard

    for cls in (BarChart, Heatmap, HBarList):
        assert isinstance(cls(), QWidget)
    assert isinstance(StatCard("Focus time"), QWidget)
    recap = WeeklyRecapCard(shell)
    assert hasattr(recap, "dismissed") and isinstance(recap.should_show(), bool)
    assert isinstance(IdleView(shell), QWidget) and isinstance(ActiveView(shell), QWidget)
    pill = FocusPill(shell)
    pill.hide()


def test_focus_page_shell_hooks(shell: AppContext) -> None:  # noqa: F811
    page = shell.window.pages["focus"]  # type: ignore[union-attr]
    for name in ("start_from_input", "focus_task_input", "set_nudge_banner", "on_shown"):
        assert callable(getattr(page, name, None)), name
    assert callable(getattr(shell.window.pages["habits"], "new_habit", None))  # type: ignore[union-attr]


def test_pure_modules_return_sensible_values(shell: AppContext, tmp_path: Path) -> None:  # noqa: F811
    from madhura.core import analytics, habits
    from madhura.data import export, legacy_import
    from madhura.platform import autostart

    hid = shell.repo.add_habit("Read", time="07:30")
    habit = shell.repo.get_habit(hid)
    today = date(2026, 10, 14)
    assert habits.is_scheduled(habit, today) is True
    assert habits.group_of(habit) == "morning"
    assert len(habits.last_7(habit, [], today)) == 7
    assert isinstance(habits.minutes_late(habit, datetime(2026, 10, 14, 8, 0)), int)

    assert analytics.focus_score([]) is None
    assert analytics.came_back([]) == 0
    assert analytics.period_delta(10, 0) is None
    assert len(analytics.daily_totals([], date(2026, 10, 8), today, 4)) == 7
    matrix = analytics.hour_weekday_matrix([])
    assert len(matrix) == 7 and all(len(row) == 24 for row in matrix)

    csv_path = tmp_path / "s.csv"
    export.export_sessions_csv(csv_path, shell.repo)
    assert csv_path.exists()
    json_path = tmp_path / "all.json"
    export.export_all_json(json_path, shell.repo)
    assert json_path.read_text(encoding="utf-8").strip().startswith("{")

    assert isinstance(legacy_import.find_legacy_habits(), list)
    fixture = Path(__file__).parent / "fixtures" / "legacy_habits.json"
    if fixture.exists():
        added = legacy_import.import_legacy_habits(fixture, shell.repo)
        assert isinstance(added, int) and added >= 0

    assert isinstance(autostart.is_supported(), bool)
    assert isinstance(autostart.is_enabled(), bool)
    assert autostart.status() in ("on", "off", "disabled_by_os", "needs_repair")
    assert isinstance(autostart.launch_command(), list)


def test_service_constructors_take_ctx() -> None:
    from madhura.services.away_service import AwayService
    from madhura.services.checkin_service import CheckInService
    from madhura.services.habit_reminders import HabitReminderService
    from madhura.services.nudge_service import NudgeService
    from madhura.ui.overlays.pill import PillController

    for cls in (AwayService, CheckInService, HabitReminderService, NudgeService, PillController):
        params = list(inspect.signature(cls.__init__).parameters)
        assert params[1] == "ctx", cls
