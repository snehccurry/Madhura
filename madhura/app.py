"""Application bootstrap (spec §5.3, §7.1, §7.4, §7.5, §7.7–7.9).

``main()`` is a thin wrapper around three testable steps::

    ctx = create_context(app, data_dir, clock)      # repo, settings, theme, engine, sound, …
    window = create_main_window(ctx)                # sets ctx.window
    start_services(ctx)                             # pill, check-ins, nudges, away, habits; restore

Startup order: AUMID → platform env (Wayland → "xcb;wayland") → single instance → logging → QApplication
(Fusion, no quit on last window, icon) → fonts → staged restore → DB/settings/theme →
context → main window → tray → services → engine.restore() → show (or tray-only) →
onboarding → pending session-complete sheet.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from madhura import APP_ID, APP_NAME, __version__, paths
from madhura.context import AppContext, AppEvents
from madhura.core.clock import Clock, SystemClock
from madhura.platform import IS_LINUX
from madhura.platform import windows as win_platform

if TYPE_CHECKING:
    from madhura.data.db import DatabaseUnavailable
    from madhura.ui.main_window import MainWindow

__all__ = [
    "main",
    "parse_args",
    "create_context",
    "create_main_window",
    "start_services",
    "after_show",
    "flush_data",
    "shutdown",
    "DayWatcher",
    "resolve_tray_available",
]

log = logging.getLogger(__name__)

DAY_CHECK_MS = 60_000
TRAY_RETRY_MS = 1_000
TRAY_RETRY_COUNT = 10
RECOVERED_FROM_KEY = "recovered_from"


# =========================================================================== helpers


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse Madhura's flags (unknown args, e.g. Qt's ``-platform``, are ignored)."""
    parser = argparse.ArgumentParser(prog="madhura", add_help=True)
    parser.add_argument("--autostart", action="store_true", help="launched at login: start in the tray")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the tray (or minimized)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args, _unknown = parser.parse_known_args(list(argv))
    return args


def resolve_tray_available(override: bool | None = None) -> bool:
    """*override*, else MADHURA_FORCE_TRAY, else ``QSystemTrayIcon.isSystemTrayAvailable()``."""
    if override is not None:
        return bool(override)
    forced = paths.force_tray()
    if forced is not None:
        return forced
    try:
        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except RuntimeError:
        return False


#: Qt tries each entry in order. xcb needs libxcb-cursor0 (Qt ≥ 6.5), which many Wayland desktops
#: don't install; a bare "xcb" would then abort in qFatal instead of falling back to Wayland.
XWAYLAND_QPA = "xcb;wayland"


def _prepare_platform_env() -> None:
    """Linux: prefer xcb under Wayland (tray, window raising and positioning work there), falling
    back to the native Wayland plugin when xcb can't load."""
    if IS_LINUX and os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = XWAYLAND_QPA


def _app_icon() -> QIcon:
    """Bundled icon.png/ico when present, else the programmatic tray ring."""
    for name in ("icon.ico", "icon.png"):
        path = paths.asset(name)
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    from madhura.ui.tray import tray_icon_for

    return tray_icon_for("idle", 1.0)


# =========================================================================== day watcher


class DayWatcher(QObject):
    """Emits ``events.day_changed`` when ``ctx.today()`` rolls over (checked every 60 s, after
    away_detected and when day_start_hour changes) and takes the daily backup."""

    def __init__(self, ctx: AppContext, interval_ms: int = DAY_CHECK_MS, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._today = ctx.today()
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self.check)

    @property
    def today(self) -> Any:
        """Last logical date seen."""
        return self._today

    def start(self) -> None:
        """Start the periodic check."""
        self._timer.start()

    def stop(self) -> None:
        """Stop the periodic check."""
        self._timer.stop()

    def is_running(self) -> bool:
        """True while the timer runs."""
        return self._timer.isActive()

    def check(self) -> bool:
        """Emit ``day_changed`` (after a daily backup) if the logical day changed. Returns True then."""
        ctx = self._ctx
        if ctx.repo.closed:
            return False
        today = ctx.today()
        if today == self._today:
            return False
        self._today = today
        _daily_backup(ctx)
        ctx.events.day_changed.emit()
        return True


def _daily_backup(ctx: AppContext) -> None:
    from madhura.data import backup

    try:
        backup.daily_backup(ctx.repo.conn, today=ctx.today(), dest_dir=ctx.data_dir / "backups")
    except (OSError, sqlite3.Error):
        log.exception("Daily backup failed")


# =========================================================================== context


def create_context(
    app: Any,
    data_dir: str | os.PathLike[str] | Path,
    clock: Clock | None = None,
    *,
    tray_available: bool | None = None,
) -> AppContext:
    """Build the :class:`AppContext`: staged restore → repo → settings → theme → engine → services.

    Everything lives under *data_dir* (``madhura.db``, ``settings.json``, ``backups/``, ``cache/``).
    Applies the theme to *app*, seeds reduce-motion on first run from the OS animation setting and
    takes today's backup. Feature services are created later by :func:`start_services`.
    """
    from madhura.data import backup
    from madhura.data.repository import Repository
    from madhura.data.settings import SettingsStore
    from madhura.services.attention import AttentionMonitor
    from madhura.services.engine import FocusEngine
    from madhura.services.notifier import Notifier
    from madhura.services.sound import SoundPlayer
    from madhura.ui.theme import ThemeManager

    clock = clock or SystemClock()
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    db_file = data / "madhura.db"
    restored = backup.apply_staged_restore(db_file, clock)

    settings_file = data / "settings.json"
    first_run = not settings_file.exists()
    settings = SettingsStore(settings_file)
    if first_run and not win_platform.client_animations_enabled():
        settings.update(reduce_motion=True)

    repo = Repository(db_file, clock=clock)

    theme = ThemeManager(settings.get("theme"), settings.get("accent"), settings.get("reduce_motion"))
    if app is not None:
        theme.apply(app)

    events = AppEvents()
    attention = AttentionMonitor(clock, repo)
    sound = SoundPlayer(settings, cache_dir=data / "cache", attention_getter=attention.state)
    notifier = Notifier()
    engine = FocusEngine(repo, settings, clock)

    ctx = AppContext(
        app=app,
        clock=clock,
        repo=repo,
        settings=settings,
        theme=theme,
        engine=engine,
        sound=sound,
        notifier=notifier,
        events=events,
        attention=attention,
        data_dir=data,
        tray_available=resolve_tray_available(tray_available),
    )
    ctx.flags["restored_backup"] = restored
    ctx.flags["first_run"] = first_run
    repo.today_fn = ctx.today
    notifier.set_tray_getter(lambda: ctx.tray)

    def on_setting(key: str) -> None:
        try:
            if key == "theme":
                theme.set_theme(settings.get("theme"))
            elif key == "accent":
                theme.set_accent(settings.get("accent"))
            elif key == "reduce_motion":
                theme.set_reduce_motion(settings.get("reduce_motion"))
        except ValueError:
            log.exception("Could not apply setting %s", key)
        events.data_changed.emit("settings")

    settings.changed.connect(on_setting)
    _daily_backup(ctx)
    return ctx


def create_main_window(ctx: AppContext) -> MainWindow:
    """Create the main window (not shown) and set ``ctx.window``."""
    from madhura.ui.main_window import MainWindow

    window = MainWindow(ctx)
    ctx.window = window
    return window


def _construct(ctx: AppContext, attr: str, factory: Callable[[AppContext], object]) -> None:
    try:
        setattr(ctx, attr, factory(ctx))
    except Exception:  # noqa: BLE001 - one broken feature service must not stop the app
        log.exception("Could not start %s", attr)
        setattr(ctx, attr, None)


# Feature services are imported here, through literal import statements, rather than by module
# name: a frozen build's import analysis (PyInstaller) only follows the former.


def _pill(ctx: AppContext) -> object:
    from madhura.ui.overlays.pill import PillController

    return PillController(ctx)


def _checkins(ctx: AppContext) -> object:
    from madhura.services.checkin_service import CheckInService

    return CheckInService(ctx)


def _nudges(ctx: AppContext) -> object:
    from madhura.services.nudge_service import NudgeService

    return NudgeService(ctx)


def _away(ctx: AppContext) -> object:
    from madhura.services.away_service import AwayService

    return AwayService(ctx)


def _habit_reminders(ctx: AppContext) -> object:
    from madhura.services.habit_reminders import HabitReminderService

    return HabitReminderService(ctx)


def start_services(ctx: AppContext) -> None:
    """Create the feature services, connect app-level signals, then ``engine.restore()`` (last)."""
    _construct(ctx, "pill", _pill)
    _construct(ctx, "checkins", _checkins)
    _construct(ctx, "nudges", _nudges)
    _construct(ctx, "away", _away)
    _construct(ctx, "habit_reminders", _habit_reminders)

    events = ctx.events
    engine = ctx.engine
    engine.session_started.connect(lambda _sid: events.data_changed.emit("sessions"))
    engine.session_finished.connect(lambda *_args: events.data_changed.emit("sessions"))
    engine.session_extended.connect(lambda _secs: events.data_changed.emit("sessions"))

    watcher = DayWatcher(ctx)
    ctx.day_watcher = watcher
    engine.away_detected.connect(lambda _gap: watcher.check())
    ctx.settings.changed.connect(lambda key: watcher.check() if key == "day_start_hour" else None)
    watcher.start()

    engine.restore()


def after_show(ctx: AppContext) -> None:
    """Post-show steps: one-time notices, onboarding sheet (first run), pending session-complete."""
    window = ctx.window
    if window is None:
        return
    if ctx.flags.get("restored_backup"):
        window.show_toast("Restored your data from the backup.", "success")
    try:
        recovered = ctx.repo.kv_get(RECOVERED_FROM_KEY)
    except sqlite3.Error:
        recovered = None
    if recovered:
        window.show_toast(
            f"Your database was damaged, so Madhura started fresh. The old file was kept as {recovered}.",
            "warning",
            duration_ms=10_000,
        )
        ctx.repo.kv_set(RECOVERED_FROM_KEY, None)
    if not ctx.settings.get("onboarded"):
        try:
            from madhura.ui.overlays.onboarding import OnboardingSheet

            window.show_sheet(OnboardingSheet(ctx))
        except Exception:  # noqa: BLE001 - onboarding is optional
            log.exception("Could not show onboarding")
    try:
        from madhura.ui.overlays.session_complete import show_pending_complete

        show_pending_complete(ctx)
    except Exception:  # noqa: BLE001 - a summary sheet must never block startup
        log.exception("Could not show the pending session summary")


# =========================================================================== quit


def flush_data(ctx: AppContext) -> None:
    """Persist everything now without closing (``commitDataRequest``; never cancels)."""
    window = ctx.window
    if window is not None and hasattr(window, "flush_toasts"):
        try:
            window.flush_toasts()
        except Exception:  # noqa: BLE001 - undo callbacks are feature code; keep flushing the rest
            log.exception("Flushing toasts failed")
    if not ctx.repo.closed:
        try:
            # Notes typed in an open "Session complete" sheet; the sheet itself comes back on
            # the next launch (kv pending_complete) for "Mark done" to be confirmed.
            from madhura.ui.overlays.session_complete import save_open_sheets

            save_open_sheets(ctx)
        except Exception:  # noqa: BLE001 - feature code; keep flushing the rest
            log.exception("Saving the open session summaries failed")
    ctx.settings.flush()
    if not ctx.repo.closed:
        try:
            ctx.engine.checkpoint()
        except sqlite3.Error:
            log.exception("Engine checkpoint failed")


def shutdown(ctx: AppContext) -> None:
    """``aboutToQuit``: flush settings, checkpoint the engine, commit undo-deletes, hide the tray,
    stop timers and close the database. Idempotent."""
    if ctx.flags.get("shut_down"):
        return
    ctx.flags["shut_down"] = True
    window = ctx.window
    if window is not None and hasattr(window, "save_geometry"):
        window.save_geometry()
    flush_data(ctx)
    ctx.engine.stop_timer()
    ctx.attention.stop()
    if ctx.day_watcher is not None:
        ctx.day_watcher.stop()
    if ctx.tray is not None:
        ctx.tray.hide()
    ctx.repo.close()
    log.info("Madhura shut down cleanly")


# =========================================================================== main


def _make_error_reporter(ctx_holder: list[AppContext]) -> Any:
    def report(message: str) -> None:
        from madhura.ui import dialogs

        ctx = ctx_holder[0] if ctx_holder else None
        window = ctx.window if ctx is not None else None
        detail = f"{message}\n\nThe details were saved to {paths.log_dir() / 'madhura.log'}."
        if window is not None and window.is_main_visible():
            window.show_toast(
                "Something went wrong — Madhura saved the details to its log.",
                "error",
                "Details",
                lambda: dialogs.show_error(window, "Something went wrong", detail),  # type: ignore[arg-type]
                8000,
            )
        else:
            dialogs.show_error(None, "Something went wrong", detail)

    return report


class _TrayRetry(QObject):
    """Linux autostart: the tray host may appear a few seconds after login; re-check for 10 s."""

    def __init__(self, ctx: AppContext, start_hidden: bool) -> None:
        super().__init__(ctx.events)
        self._ctx = ctx
        self._start_hidden = start_hidden
        self._left = TRAY_RETRY_COUNT
        self._timer = QTimer(self)
        self._timer.setInterval(TRAY_RETRY_MS)
        self._timer.timeout.connect(self._check)
        self._timer.start()

    def _check(self) -> None:
        self._left -= 1
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._timer.stop()
            ctx = self._ctx
            ctx.tray_available = True
            _create_tray(ctx)
            window = ctx.window
            if self._start_hidden and window is not None and not window.is_main_active():
                window.hide_main()
            return
        if self._left <= 0:
            self._timer.stop()


def _create_tray(ctx: AppContext) -> None:
    if ctx.tray is not None:
        return
    try:
        from madhura.ui.tray import Tray

        ctx.tray = Tray(ctx)
        ctx.tray.show()
    except Exception:  # noqa: BLE001 - the app works without a tray
        log.exception("Could not create the tray icon")
        ctx.tray = None


def _show_startup_error(argv: list[str], title: str, message: str) -> None:
    """Modal error before the main window exists (themed with the defaults)."""
    app = QApplication.instance() or QApplication(argv)
    app.setStyle("Fusion")
    from madhura.ui import dialogs, fonts
    from madhura.ui.theme import ThemeManager

    fonts.apply_app_font(app)
    ThemeManager().apply(app)
    dialogs.show_error(None, title, message)


def _show_lock_error(argv: list[str], message: str) -> None:
    _show_startup_error(argv, f"{APP_NAME} is already running", message)


def _database_unavailable_message(exc: DatabaseUnavailable) -> str:
    return (
        f"Another program may be using {exc.path.name} (a database browser, backup or sync tool), "
        f"or its folder isn't reachable yet. Nothing was changed.\n\n"
        f"Close that program, or wait a moment, then start {APP_NAME} again.\n\n"
        f"Folder: {exc.path.parent}\nDetails: {exc.reason}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run Madhura; returns the process exit code."""
    argv_list = list(sys.argv if argv is None else argv)
    if not argv_list:
        argv_list = [APP_NAME.lower()]
    args = parse_args(argv_list[1:])

    win_platform.set_app_user_model_id(APP_ID)
    _prepare_platform_env()

    from madhura.services.single_instance import InstanceStatus, SingleInstance

    guard = SingleInstance()
    status = guard.acquire()
    if status is not InstanceStatus.PRIMARY:
        if status is InstanceStatus.FAILED:
            message = guard.error or "Madhura is already running."
            if sys.stderr is not None:
                print(message, file=sys.stderr)
            try:
                _show_lock_error(argv_list, message)
            except Exception:  # noqa: BLE001 - best effort; the exit code still reports the failure
                log.debug("Could not show the lock error dialog", exc_info=True)
        return guard.exit_code(status)

    from madhura.log import install_excepthook, setup_logging

    setup_logging()
    log.info("Starting %s %s", APP_NAME, __version__)

    app = QApplication.instance() or QApplication(argv_list)
    guard.listen()  # as early as possible: a racing second launch waits for this (single_instance)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("madhura")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(_app_icon())
    win_platform.register_app_user_model_id(APP_ID, APP_NAME, paths.asset("icon.png"))

    from madhura.ui import fonts
    from madhura.ui.widgets.inputs import install_wheel_guard

    fonts.apply_app_font(app)
    install_wheel_guard(app)

    holder: list[AppContext] = []
    install_excepthook(_make_error_reporter(holder))

    from madhura.data import db as dbmod

    try:
        ctx = create_context(app, paths.data_dir(), SystemClock())
    except dbmod.DatabaseUnavailable as exc:
        # Locked or unreachable is not damaged: never start fresh on top of a healthy database.
        try:
            _show_startup_error(argv_list, f"{APP_NAME} can't open its data right now", _database_unavailable_message(exc))
        except Exception:  # noqa: BLE001 - best effort; the exit code still reports the failure
            log.exception("Could not show the database error dialog")
        guard.release()
        return 1
    holder.append(ctx)
    window = create_main_window(ctx)
    if ctx.tray_available:
        _create_tray(ctx)
    start_services(ctx)

    guard.show_requested.connect(lambda: window.show_main(activate=True))
    app.aboutToQuit.connect(lambda: shutdown(ctx))
    app.commitDataRequest.connect(lambda _manager: flush_data(ctx))

    start_hidden = bool(args.autostart or args.minimized or ctx.settings.get("start_minimized"))
    if not start_hidden:
        window.show_main(activate=True)
    elif not ctx.tray_available:
        window.showMinimized()
    if args.autostart and IS_LINUX and not ctx.tray_available and paths.force_tray() is None:
        _TrayRetry(ctx, start_hidden)
    after_show(ctx)

    code = app.exec()
    shutdown(ctx)
    guard.release()
    return int(code)
