"""``madhura --self-test``: a headless check that an installation or a frozen build is complete.

CI runs it on the PyInstaller build; it works on any install::

    Madhura.exe --self-test                    # exit code 0 = everything works, 1 = it doesn't
    python -m madhura --self-test report.txt   # … and write the report to report.txt as well

It imports every module of the package (a frozen build holds only what its import analysis
found), checks the bundled assets (the app icon with all its sizes, the chime — decoded and
volume-scaled, never played) and that Qt can draw the SVG icons, then builds the real app —
context, main window, tray, feature services — visits every page, shows the onboarding,
shortcuts and wrap-up sheets, starts and abandons a focus session, and shuts down cleanly.
Any import error, missing asset, page that fell back to "This page couldn't load", unhandled
exception, ERROR log record or Qt critical message fails it.

Qt runs on the offscreen platform unless ``QT_QPA_PLATFORM`` is set; sound and the
single-instance guard are off and the data directory is a temporary folder (removed afterwards),
so the user's data, settings and a running Madhura are never touched. The report goes to stdout
when there is one (a windowed frozen build has none) and to *report* when given.
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
import sys
import tempfile
import traceback
import wave
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from types import TracebackType
from typing import Any

from madhura import APP_NAME, __version__, paths

__all__ = ["MODULES", "ASSETS", "run"]

log = logging.getLogger(__name__)

#: Every module of the package except ``madhura.__main__`` (a test keeps this list complete).
MODULES: tuple[str, ...] = (
    "madhura",
    "madhura.app",
    "madhura.context",
    "madhura.core",
    "madhura.core.analytics",
    "madhura.core.checkins",
    "madhura.core.clock",
    "madhura.core.days",
    "madhura.core.habits",
    "madhura.core.models",
    "madhura.core.nudges",
    "madhura.core.streaks",
    "madhura.core.timefmt",
    "madhura.data",
    "madhura.data.backup",
    "madhura.data.db",
    "madhura.data.export",
    "madhura.data.legacy_import",
    "madhura.data.repository",
    "madhura.data.settings",
    "madhura.log",
    "madhura.paths",
    "madhura.platform",
    "madhura.platform.attention",
    "madhura.platform.autostart",
    "madhura.platform.idle",
    "madhura.platform.windows",
    "madhura.selftest",
    "madhura.services",
    "madhura.services.attention",
    "madhura.services.away_service",
    "madhura.services.checkin_service",
    "madhura.services.engine",
    "madhura.services.habit_reminders",
    "madhura.services.notifier",
    "madhura.services.nudge_service",
    "madhura.services.single_instance",
    "madhura.services.sound",
    "madhura.ui",
    "madhura.ui.animations",
    "madhura.ui.dialogs",
    "madhura.ui.fonts",
    "madhura.ui.icons",
    "madhura.ui.main_window",
    "madhura.ui.overlays",
    "madhura.ui.overlays._panel_kit",
    "madhura.ui.overlays.away_panel",
    "madhura.ui.overlays.checkin_popup",
    "madhura.ui.overlays.habit_reminder_popup",
    "madhura.ui.overlays.nudge_popup",
    "madhura.ui.overlays.onboarding",
    "madhura.ui.overlays.pill",
    "madhura.ui.overlays.session_complete",
    "madhura.ui.overlays.wrap_up",
    "madhura.ui.pages",
    "madhura.ui.pages._habit_kit",
    "madhura.ui.pages._insights_kit",
    "madhura.ui.pages._settings_kit",
    "madhura.ui.pages.focus",
    "madhura.ui.pages.focus._idle_helpers",
    "madhura.ui.pages.focus.active_view",
    "madhura.ui.pages.focus.idle_view",
    "madhura.ui.pages.focus_page",
    "madhura.ui.pages.habit_editor",
    "madhura.ui.pages.habits_page",
    "madhura.ui.pages.insights_page",
    "madhura.ui.pages.settings_page",
    "madhura.ui.shortcuts_sheet",
    "madhura.ui.sidebar",
    "madhura.ui.theme",
    "madhura.ui.tray",
    "madhura.ui.widgets",
    "madhura.ui.widgets._common",
    "madhura.ui.widgets.banner",
    "madhura.ui.widgets.buttons",
    "madhura.ui.widgets.card",
    "madhura.ui.widgets.charts",
    "madhura.ui.widgets.checkbox",
    "madhura.ui.widgets.chips",
    "madhura.ui.widgets.empty_state",
    "madhura.ui.widgets.inputs",
    "madhura.ui.widgets.labels",
    "madhura.ui.widgets.list_row",
    "madhura.ui.widgets.menu",
    "madhura.ui.widgets.page",
    "madhura.ui.widgets.popover",
    "madhura.ui.widgets.popup",
    "madhura.ui.widgets.progress_ring",
    "madhura.ui.widgets.recap",
    "madhura.ui.widgets.select",
    "madhura.ui.widgets.settings_row",
    "madhura.ui.widgets.sheet",
    "madhura.ui.widgets.stat_card",
    "madhura.ui.widgets.toast",
    "madhura.ui.widgets.toggle",
)

#: Bundled files the app needs at run time (relative to ``madhura/assets``).
ASSETS: tuple[str, ...] = ("icon.png", "icon.ico", "sounds/chime.wav")

#: The feature services ``app.start_services`` puts on the context.
_SERVICES = ("pill", "checkins", "nudges", "away", "habit_reminders")


class _Report:
    """Collects check results; failures decide the exit code."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures = 0
        self.checks = 0

    def info(self, text: str) -> None:
        self.lines.append(f"      {text}")

    def ok(self, text: str) -> None:
        self.checks += 1
        self.lines.append(f"ok    {text}")

    def fail(self, text: str) -> None:
        self.checks += 1
        self.failures += 1
        self.lines.append(f"FAIL  {text}")

    def check(self, condition: object, text: str, detail: str = "") -> bool:
        if condition:
            self.ok(text)
            return True
        self.fail(f"{text}{f' - {detail}' if detail else ''}")
        return False

    def step(self, text: str, fn: Callable[[], object], *, quiet: bool = False) -> Any:
        """Run *fn* and return its result; an exception fails *text* (traceback included).
        Success is reported unless *quiet* (when *fn* reports its own checks)."""
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - the self-test reports every failure
            self.fail(f"{text} - {type(exc).__name__}: {exc}")
            self.lines.extend(f"        {line}" for line in traceback.format_exc().rstrip().splitlines())
            return None
        if not quiet:
            self.ok(text)
        return result

    def text(self) -> str:
        verdict = "OK" if not self.failures else f"FAILED ({self.failures} of {self.checks} checks)"
        return "\n".join([*self.lines, f"{APP_NAME} {__version__} self-test: {verdict}"]) + "\n"


class _Capture(logging.Handler):
    """Records WARNING+ log records; ERROR+ ones fail the self-test."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _isolated(data: Path) -> Iterator[None]:
    """Temporary environment (offscreen Qt unless set, no sound, no single instance, temp data)."""
    wanted = {
        paths.ENV_DATA_DIR: str(data),
        paths.ENV_NO_SOUND: "1",
        paths.ENV_NO_SINGLE_INSTANCE: "1",
    }
    if not os.environ.get("QT_QPA_PLATFORM"):
        wanted["QT_QPA_PLATFORM"] = "offscreen"
    saved = {key: os.environ.get(key) for key in wanted}
    os.environ.update(wanted)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _check_imports(report: _Report) -> None:
    missing = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - report every module that fails
            missing.append(f"{name} ({type(exc).__name__}: {exc})")
    report.check(not missing, f"import {len(MODULES)} modules", "; ".join(missing))


def _check_assets(report: _Report) -> None:
    for rel in ASSETS:
        path = paths.asset(rel)
        report.check(path.is_file(), f"asset {rel}", f"not found at {path}")


def _check_qt(report: _Report, app: Any) -> None:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImageReader

    from madhura import app as appmod
    from madhura.ui import icons

    report.info(f"Qt {_qt_version()} on the {app.platformName()!r} platform")
    formats = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
    report.check({"png", "ico"} <= formats, "Qt image formats png + ico", f"have {sorted(formats)}")
    icon = appmod._app_icon()  # noqa: SLF001 - the exact icon main() installs
    sizes = sorted({s.width() for s in icon.availableSizes()})
    report.check(sizes and sizes[0] <= 16 and sizes[-1] >= 256, "app icon 16-256 px", f"sizes {sizes}")
    image = icons.pixmap("logo", 24, "#ffffff", 1.0).toImage()
    drawn = any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(image.height()))
    report.check(drawn and image.size() == QSize(24, 24), "SVG icons render")


def _check_sound(report: _Report, ctx: Any) -> None:
    def scale() -> None:
        path = ctx.sound.scaled_wav(40)
        with wave.open(str(path), "rb") as wav:
            if wav.getnframes() <= 0:
                raise ValueError(f"{path.name} has no audio frames")

    report.step("chime decodes and scales (not played)", scale)


def _qt_version() -> str:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion

    return f"{qVersion()} (PySide6 {pyside_version})"


def _settle(app: Any, rounds: int = 3) -> None:
    for _ in range(rounds):
        app.processEvents()


def _run_app(report: _Report, app: Any, data: Path) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    from madhura import app as appmod
    from madhura.core.clock import SystemClock
    from madhura.ui.pages import PAGE_KEYS
    from madhura.ui.widgets.empty_state import EmptyState
    from madhura.ui.widgets.popup import PopupStack

    ctx = report.step("create the app context", lambda: appmod.create_context(app, data, SystemClock(), tray_available=True))
    if ctx is None:
        return
    window = None
    try:
        ctx.settings.update(reduce_motion=True)  # instant transitions; this data folder is temporary
        window = report.step("build the main window", lambda: appmod.create_main_window(ctx))
        if window is None:
            return
        appmod._create_tray(ctx)  # noqa: SLF001 - the same path main() takes
        report.check(ctx.tray is not None, "tray icon and menu")
        report.step("start the feature services", lambda: appmod.start_services(ctx))
        missing = [name for name in _SERVICES if getattr(ctx, name, None) is None]
        report.check(not missing, "feature services", f"not running: {', '.join(missing)}")
        _check_sound(report, ctx)

        window.show_main(activate=True)
        _settle(app)
        report.step("after-show (onboarding sheet)", lambda: appmod.after_show(ctx))
        _settle(app)
        report.check(window.has_sheet(), "onboarding sheet shows on first run")
        window.close_sheet()
        _settle(app)

        for key in PAGE_KEYS:
            report.step(f"show the {key} page", partial(window.show_page, key))
            _settle(app)
            page = window.pages[key]
            report.check(
                not isinstance(page, EmptyState) and window.current_page() == key,
                f"{key} page ({type(page).__name__})",
                'fell back to "This page couldn\'t load"',
            )

        report.step("shortcuts sheet", window.show_shortcuts)
        _settle(app)
        window.close_sheet()

        def wrap_up() -> None:
            from madhura.ui.overlays.wrap_up import WrapUpSheet

            window.show_sheet(WrapUpSheet(ctx))

        report.step("wrap-up sheet", wrap_up)
        _settle(app)
        window.close_sheet()
        _settle(app)

        window.show_page("focus")
        sid = report.step("start a focus session", lambda: ctx.engine.start("Self-test", 25))
        _settle(app)
        report.check(sid is not None and ctx.engine.is_active, "focus session runs (active view, mini timer)")
        report.step("abandon the focus session", ctx.engine.abandon)
        _settle(app)
        report.check(not ctx.engine.is_active, "back to idle")
    finally:
        report.step("shut down", lambda: appmod.shutdown(ctx))
        if window is not None:
            window.hide()
            window.deleteLater()
        PopupStack.reset()
        _settle(app)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _emit(text: str, report_path: str | os.PathLike[str] | None) -> None:
    stream = sys.stdout
    if stream is not None:
        try:
            try:
                stream.write(text)
            except UnicodeEncodeError:  # e.g. a non-ASCII folder name on a legacy console code page
                encoding = getattr(stream, "encoding", None) or "ascii"
                stream.write(text.encode(encoding, "replace").decode(encoding))
            stream.flush()
        except (OSError, ValueError):
            pass
    if report_path:
        try:
            Path(report_path).write_text(text, encoding="utf-8")
        except OSError as exc:
            if sys.stderr is not None:
                print(f"Could not write {report_path}: {exc}", file=sys.stderr)


def run(report_path: str | os.PathLike[str] | None = None, argv: Sequence[str] | None = None) -> int:
    """Run the self-test; returns the process exit code (0 = OK, 1 = failed)."""
    report = _Report()
    frozen = bool(getattr(sys, "frozen", False))
    report.info(
        f"{APP_NAME} {__version__} | Python {platform.python_version()} | {sys.platform}"
        f" | {'frozen' if frozen else 'from source'}"
    )
    report.info(f"assets: {paths.asset('')}")
    data = Path(tempfile.mkdtemp(prefix="madhura-self-test-"))
    capture = _Capture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(capture)
    root.setLevel(logging.INFO)
    hook_errors: list[str] = []
    old_hook = sys.excepthook

    def hook(kind: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        hook_errors.append("".join(traceback.format_exception(kind, exc, tb)).rstrip())

    sys.excepthook = hook
    qt_critical: list[str] = []
    message_handler: list[Any] = []  # [previous handler] once ours is installed
    try:
        with _isolated(data):
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
            from PySide6.QtWidgets import QApplication

            def on_qt_message(kind: QtMsgType, _context: Any, message: str) -> None:
                if kind in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                    qt_critical.append(message)

            message_handler.append(qInstallMessageHandler(on_qt_message))
            _check_imports(report)
            _check_assets(report)
            app = QApplication.instance()
            if app is None:
                app = QApplication(list(argv) if argv else [APP_NAME.lower()])
                app.setApplicationName(APP_NAME)
                app.setOrganizationName(APP_NAME)
                app.setApplicationVersion(__version__)
                app.setStyle("Fusion")
                app.setQuitOnLastWindowClosed(False)
            from madhura import app as appmod
            from madhura.ui import fonts

            app.setWindowIcon(appmod._app_icon())  # noqa: SLF001
            fonts.apply_app_font(app)
            report.step("Qt, icons and image formats", lambda: _check_qt(report, app), quiet=True)
            _run_app(report, app, data)
    except Exception as exc:  # noqa: BLE001 - whatever happens, report it
        report.fail(f"self-test aborted - {type(exc).__name__}: {exc}")
        report.lines.extend(f"        {line}" for line in traceback.format_exc().rstrip().splitlines())
    finally:
        sys.excepthook = old_hook
        root.removeHandler(capture)
        root.setLevel(old_level)
        if message_handler:
            from PySide6.QtCore import qInstallMessageHandler

            qInstallMessageHandler(message_handler[0])
        shutil.rmtree(data, ignore_errors=True)

    errors = [r for r in capture.records if r.levelno >= logging.ERROR]
    for record in capture.records:
        if record.levelno < logging.ERROR:
            report.info(f"warning from {record.name}: {record.getMessage()}")
    report.check(not errors, "no errors logged", f"{len(errors)} error(s)")
    for record in errors:
        report.lines.append(f"        {record.name}: {record.getMessage()}")
        if record.exc_info:
            text = "".join(traceback.format_exception(*record.exc_info)).rstrip()
            report.lines.extend(f"          {line}" for line in text.splitlines())
    report.check(not hook_errors, "no unhandled exceptions", f"{len(hook_errors)} exception(s)")
    for text in hook_errors:
        report.lines.extend(f"        {line}" for line in text.splitlines())
    report.check(not qt_critical, "no Qt critical messages", "; ".join(qt_critical))
    _emit(report.text(), report_path)
    return 1 if report.failures else 0
