"""App shell: create_context / start_services / restore / day watcher / shutdown / main()."""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from madhura import app as appmod
from madhura.context import AppContext, AppEvents, FakeWindowController, PAGE_KEYS, WindowController
from madhura.core.clock import FakeClock
from madhura.services.single_instance import server_name_for
from test_shell_support import advance, dispose_shell, make_shell, shell  # noqa: F401


# --------------------------------------------------------------------------- context


def test_create_context_builds_everything(shell: AppContext, data_dir: Path) -> None:  # noqa: F811
    ctx = shell
    assert ctx.data_dir == data_dir
    assert (data_dir / "madhura.db").exists()
    assert ctx.repo.today_fn == ctx.today
    assert ctx.tray_available is False
    for name in ("pill", "checkins", "nudges", "away", "habit_reminders", "window", "day_watcher"):
        assert getattr(ctx, name) is not None, name
    assert isinstance(ctx.window, WindowController)
    assert ctx.day_watcher.is_running()


def test_today_uses_logical_day(qapp: QApplication, data_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 10, 14, 2, 30))
    ctx = make_shell(qapp, data_dir, clock, services=False)
    try:
        assert ctx.today() == date(2026, 10, 13)
        assert ctx.day_bounds() == (datetime(2026, 10, 13, 4, 0), datetime(2026, 10, 14, 4, 0))
        ctx.settings.update(day_start_hour=0)
        assert ctx.today() == date(2026, 10, 14)
        assert ctx.day_bounds(date(2026, 10, 1))[0] == datetime(2026, 10, 1, 0, 0)
        # add_task without plan_date uses the logical day
        ctx.settings.update(day_start_hour=4)
        tid = ctx.repo.add_task("Plan the week")
        assert ctx.repo.get_task(tid).plan_date == date(2026, 10, 13)
    finally:
        dispose_shell(ctx, qapp)


def test_use_24h_follows_setting(shell: AppContext) -> None:  # noqa: F811
    shell.settings.update(time_format="24h")
    assert shell.use_24h() is True
    shell.settings.update(time_format="12h")
    assert shell.use_24h() is False
    shell.settings.update(time_format="system")
    assert isinstance(shell.use_24h(), bool)


def test_settings_drive_theme_and_emit_data_changed(shell: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    seen: list[str] = []
    shell.events.data_changed.connect(seen.append)
    shell.settings.update(theme="light")
    assert shell.theme.theme == "light" and shell.theme.scheme == "light"
    shell.settings.update(accent="teal")
    assert shell.theme.accent == "teal"
    shell.settings.update(reduce_motion=False)
    assert shell.theme.reduce_motion is False
    assert seen.count("settings") == 3


def test_first_run_seeds_reduce_motion(qapp: QApplication, data_dir: Path, fake_clock: FakeClock, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(appmod.win_platform, "client_animations_enabled", lambda: False)
    ctx = appmod.create_context(qapp, data_dir, fake_clock, tray_available=False)
    try:
        assert ctx.flags["first_run"] is True
        assert ctx.settings.get("reduce_motion") is True
        ctx.settings.update(reduce_motion=False)
        ctx.settings.flush()
    finally:
        appmod.shutdown(ctx)
    ctx2 = appmod.create_context(qapp, data_dir, fake_clock, tray_available=False)
    try:
        assert ctx2.flags["first_run"] is False
        assert ctx2.settings.get("reduce_motion") is False  # never re-seeded
    finally:
        appmod.shutdown(ctx2)
        qapp.setStyleSheet("")


def test_tray_available_resolution(monkeypatch) -> None:  # noqa: ANN001
    assert appmod.resolve_tray_available(True) is True
    assert appmod.resolve_tray_available(False) is False
    monkeypatch.setenv("MADHURA_FORCE_TRAY", "1")
    assert appmod.resolve_tray_available() is True
    monkeypatch.setenv("MADHURA_FORCE_TRAY", "0")
    assert appmod.resolve_tray_available() is False


def test_daily_backup_on_create(shell: AppContext, data_dir: Path) -> None:  # noqa: F811
    assert (data_dir / "backups" / "madhura-2026-10-14.db").exists()


def test_staged_restore_is_applied(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> None:
    from madhura.data import backup

    ctx = make_shell(qapp, data_dir, fake_clock, services=False)
    ctx.repo.add_habit("Read 10 pages")
    snapshot = backup.backup_now(ctx.repo.conn, data_dir / "backups", "snap")
    ctx.repo.add_habit("Second habit")
    dispose_shell(ctx, qapp)
    backup.stage_restore(snapshot, data_dir / "madhura.db")

    ctx2 = make_shell(qapp, data_dir, fake_clock, services=False)
    try:
        assert ctx2.flags["restored_backup"] is True
        assert [h.name for h in ctx2.repo.habits()] == ["Read 10 pages"]
        appmod.after_show(ctx2)
        assert "Restored" in ctx2.window.toasts.text()
    finally:
        dispose_shell(ctx2, qapp)


def test_events_and_page_keys() -> None:
    ev = AppEvents()
    got: list[tuple[str, str]] = []
    ev.toast.connect(lambda t, k: got.append((t, k)))
    ev.toast.emit("Hi", "info")
    assert got == [("Hi", "info")]
    assert PAGE_KEYS == ("focus", "habits", "insights", "settings")


def test_fake_window_controller_records() -> None:
    fake = FakeWindowController(visible=False, active=False)
    assert isinstance(fake, WindowController)
    assert not fake.is_main_visible()
    fake.show_main("habits", activate=True)
    assert fake.is_main_active() and fake.current_page() == "habits"
    fake.show_toast("Saved", "success")
    fake.set_nudge_banner(True)
    fake.focus_task_input()
    assert fake.toasts == [("Saved", "success", None)]
    assert fake.nudge_banner and fake.task_focus_requests == 1
    assert fake.called("show_main") == 1
    fake.hide_main()
    assert not fake.is_main_visible()
    fake.quit_app()
    assert fake.quit_requested


def test_fake_window_controller_sheets(qapp: QApplication) -> None:
    from madhura.ui.widgets.sheet import SheetContent

    fake = FakeWindowController()
    a, b = SheetContent(), SheetContent()
    finished: list[str] = []
    a.finished.connect(lambda: finished.append("a"))
    fake.show_sheet(a)
    fake.show_sheet(b)
    assert fake.has_sheet()
    fake.close_sheet()
    assert finished == ["a"] and fake.sheets == [b]


# --------------------------------------------------------------------------- services / restore


def test_engine_signals_emit_sessions_data_changed(shell: AppContext) -> None:  # noqa: F811
    seen: list[str] = []
    shell.events.data_changed.connect(seen.append)
    sid = shell.engine.start("Write", 25)
    assert sid is not None
    shell.engine.extend(5)
    advance(shell, 120)
    shell.engine.finish()
    assert seen.count("sessions") >= 3


def _seed_running_session(qapp: QApplication, data_dir: Path, clock: FakeClock) -> int:
    ctx = make_shell(qapp, data_dir, clock)
    sid = ctx.engine.start("Deep work", 60)
    advance(ctx, 600)
    ctx.engine.checkpoint()
    # simulate a crash: no pause, just close
    ctx.engine.stop_timer()
    dispose_shell(ctx, qapp)
    assert sid is not None
    return sid


def test_restore_after_long_gap_pauses(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> None:
    _seed_running_session(qapp, data_dir, fake_clock)
    fake_clock.advance(3 * 3600)
    away: list[int] = []
    from madhura.services import engine as engine_mod

    ctx = make_shell(qapp, data_dir, fake_clock, services=False)
    try:
        ctx.engine.away_detected.connect(away.append)
        appmod.start_services(ctx)
        assert str(ctx.engine.state) == engine_mod.EngineState.PAUSED
        assert away and away[0] >= 3 * 3600 - 5
    finally:
        dispose_shell(ctx, qapp)


def test_restore_completed_while_closed_toasts(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> None:
    sid = _seed_running_session(qapp, data_dir, fake_clock)
    # Pretend the process died after the planned end had already been reached.
    from madhura.data.repository import Repository

    repo = Repository(data_dir / "madhura.db", clock=fake_clock)
    snap = repo.kv_get("engine_snapshot")
    snap["segment_start"] = snap["last_alive"] - snap["planned_seconds"] - 5
    snap["focused_before"] = 0
    repo.kv_set("engine_snapshot", snap)
    repo.close()
    fake_clock.advance(2 * 3600)
    ctx = make_shell(qapp, data_dir, fake_clock, services=False)
    try:
        ctx.window.show_main()
        appmod.start_services(ctx)
        assert str(ctx.engine.state) == "idle"
        assert ctx.repo.get_session(sid).outcome == "completed"
        assert "finished while Madhura was closed" in ctx.window.toasts.text()
    finally:
        dispose_shell(ctx, qapp)


def test_service_failure_does_not_stop_startup(qapp: QApplication, data_dir: Path, fake_clock: FakeClock, monkeypatch) -> None:  # noqa: ANN001
    import madhura.services.away_service as away_mod

    class Broken:
        def __init__(self, _ctx: object) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(away_mod, "AwayService", Broken)
    ctx = make_shell(qapp, data_dir, fake_clock)
    try:
        assert ctx.away is None
        assert ctx.nudges is not None and ctx.habit_reminders is not None
    finally:
        dispose_shell(ctx, qapp)


# --------------------------------------------------------------------------- day watcher


def test_day_watcher_emits_and_backs_up(shell: AppContext, data_dir: Path) -> None:  # noqa: F811
    days: list[bool] = []
    shell.events.day_changed.connect(lambda: days.append(True))
    assert shell.day_watcher.check() is False
    shell.clock.set(datetime(2026, 10, 15, 3, 59))  # type: ignore[attr-defined]
    assert shell.day_watcher.check() is False  # logical day still the 14th
    shell.clock.set(datetime(2026, 10, 15, 4, 1))  # type: ignore[attr-defined]
    assert shell.day_watcher.check() is True
    assert days == [True]
    assert (data_dir / "backups" / "madhura-2026-10-15.db").exists()


def test_day_start_hour_change_triggers_check(shell: AppContext) -> None:  # noqa: F811
    days: list[bool] = []
    shell.events.day_changed.connect(lambda: days.append(True))
    shell.clock.set(datetime(2026, 10, 15, 2, 0))  # type: ignore[attr-defined]  # still the 14th (start 4)
    shell.settings.update(day_start_hour=0)  # now the 15th
    assert days == [True]


def test_away_detected_triggers_day_check(shell: AppContext) -> None:  # noqa: F811
    days: list[bool] = []
    shell.events.day_changed.connect(lambda: days.append(True))
    shell.engine.start("Night shift", 60)
    advance(shell, 60)
    shell.clock.advance(20 * 3600)  # type: ignore[attr-defined]  # laptop slept into the next day
    shell.engine.poll()
    assert str(shell.engine.state) == "paused"
    assert days == [True]


# --------------------------------------------------------------------------- shutdown


def test_shutdown_is_idempotent_and_persists(shell: AppContext, data_dir: Path) -> None:  # noqa: F811
    shell.settings.update(daily_goal_minutes=180)
    shell.engine.start("Write", 30)
    advance(shell, 120)
    appmod.shutdown(shell)
    appmod.shutdown(shell)
    assert shell.repo.closed
    assert not shell.day_watcher.is_running()
    assert '"daily_goal_minutes": 180' in (data_dir / "settings.json").read_text(encoding="utf-8")


def test_flush_data_keeps_running(shell: AppContext) -> None:  # noqa: F811
    shell.engine.start("Write", 30)
    advance(shell, 90)
    appmod.flush_data(shell)
    assert not shell.repo.closed
    snap = shell.repo.kv_get("engine_snapshot")
    assert snap and snap["state"] == "focus"


def test_after_show_onboarding_and_recovered_notice(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> None:
    ctx = make_shell(qapp, data_dir, fake_clock, onboarded=False)
    try:
        ctx.repo.kv_set("recovered_from", "madhura.corrupt-20261014-100000.db")
        ctx.window.show_main()
        appmod.after_show(ctx)
        from madhura.ui.overlays.onboarding import OnboardingSheet

        assert isinstance(ctx.window.sheet_host.current(), OnboardingSheet)
        assert "damaged" in ctx.window.toasts.text()
        assert ctx.repo.kv_get("recovered_from") is None
    finally:
        dispose_shell(ctx, qapp)


# --------------------------------------------------------------------------- main / args / single instance


def test_parse_args() -> None:
    args = appmod.parse_args(["--autostart", "-platform", "offscreen"])
    assert args.autostart and not args.minimized
    assert appmod.parse_args(["--minimized"]).minimized


def test_single_instance_name_is_stable(tmp_path: Path) -> None:
    a = server_name_for(tmp_path / "one")
    assert a == server_name_for(tmp_path / "one")
    assert a.startswith("madhura-") and len(a) == len("madhura-") + 16
    assert a != server_name_for(tmp_path / "two")


def test_main_runs_and_quits(qapp: QApplication, data_dir: Path, monkeypatch) -> None:  # noqa: ANN001
    """Full startup path (no tray, single instance disabled): shows the window, quits cleanly."""
    from madhura.ui import theme
    from madhura.ui.widgets.popup import PopupStack

    monkeypatch.setenv("MADHURA_FORCE_TRAY", "0")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    seen: dict[str, object] = {}

    def probe() -> None:
        windows = [w for w in QApplication.topLevelWidgets() if w.objectName() == "mhMainWindow" and w.isVisible()]
        seen["window"] = bool(windows)
        if windows:
            seen["title"] = windows[0].windowTitle()
            windows[0].quit_app()

    original_quit = QApplication.quit
    QTimer.singleShot(200, probe)
    QTimer.singleShot(5000, original_quit)  # safety net
    try:
        code = appmod.main(["madhura"])
    finally:
        for h in list(root.handlers):
            if h not in handlers_before:
                root.removeHandler(h)
                h.close()
        for w in QApplication.topLevelWidgets():
            if w.objectName() == "mhMainWindow":
                w.hide()
                w.deleteLater()
        PopupStack.reset()
        theme.set_current(None)
        qapp.setStyleSheet("")
    assert code == 0
    assert seen.get("window") is True and seen.get("title") == "Madhura"
    assert (data_dir / "madhura.db").exists()


def test_dunder_main_imports() -> None:
    import importlib

    mod = importlib.import_module("madhura.__main__")
    assert mod.main is appmod.main
