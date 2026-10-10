"""Render deterministic screenshots of every Madhura screen and state.

Seeds demo data (``tools/seed_demo.py``) into a temporary data directory, pins a FakeClock to a
weekday morning (Wednesday 14 Oct 2026, 10:50), builds the real app (``create_context`` →
``create_main_window`` → ``start_services``) on Qt's offscreen platform and saves a PNG of every
page, sheet, floating panel and state, in dark and light::

    python tools/screenshots.py                          # dark + light → docs/screenshots/
    python tools/screenshots.py --theme light --out /tmp/shots
    python tools/screenshots.py --scale 2                # HiDPI renders (2× pixels)
    python tools/screenshots.py --only hero,focus_idle   # just these (names: --list)
    python tools/screenshots.py --list

Files are named ``<shot>_<theme>.png``. The main window is 1280×800 logical pixels; floating
panels (pill, check-in, nudge, …) are composited on a neutral desktop-like backdrop, and
``hero_<theme>.png`` shows the main window, the mini timer and a check-in together for the
README. Output is deterministic: fixed clock and seed, reduce-motion on, 24-hour time, no text
cursor blink, background timers frozen. Runs headless; nothing outside the temp dir is touched
(launch-at-login and the old-habits scan are redirected/disabled).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "screenshots"

#: Wednesday morning, inside the default work hours.
NOW = datetime(2026, 10, 14, 10, 50)
#: Demo data: half a year of history (so every Insights range and its comparison period is
#: full); this seed has two blocks done this morning (90 of 120 min) and a 9-day goal streak.
SEED = 65
WEEKS = 26
WINDOW_SIZE = (1280, 800)
WINDOW_POS = (120, 80)
SCREEN_SIZE = (1920, 1200)  # logical; the pill and panels sit at its bottom-right
HERO_SIZE = (1600, 1000)
THEMES = ("dark", "light")

#: Every shot, in the order it is rendered (``--only`` filters the files written).
SHOTS: tuple[str, ...] = (
    "focus_idle",
    "focus_idle_full",
    "habits",
    "habit_editor",
    "insights_7d",
    "insights_30d",
    "insights_12w",
    "insights_7d_full",
    "settings_top",
    "settings_sound",
    "settings_appearance",
    "settings_data",
    "shortcuts",
    "wrap_up",
    "focus_active_checkin_inline",
    "focus_active",
    "focus_paused",
    "pill_collapsed",
    "pill_expanded",
    "pill_states",
    "checkin_popup",
    "checkin_popup_reasons",
    "away_panel",
    "hero",
    "completion_panel",
    "session_complete",
    "focus_break",
    "nudge_popup",
    "habit_reminder",
    "onboarding_1",
    "onboarding_2",
    "onboarding_3",
    "onboarding_4",
    "focus_empty",
    "habits_empty",
    "insights_empty",
)


# =========================================================================== environment


def _prepare_environment(scale: float, root: Path) -> None:
    """Headless Qt with a large virtual screen; isolate Madhura's data, sound and autostart.

    Must run before PySide6 is imported."""
    if "PySide6.QtWidgets" in sys.modules:
        raise SystemExit("tools/screenshots.py must configure Qt before PySide6 is imported")
    config = root / "screens.json"
    width, height = (round(v * scale) for v in SCREEN_SIZE)
    screen = {"name": "Desktop", "x": 0, "y": 0, "width": width, "height": height, "logicalDpi": 96, "dpr": 1}
    config.write_text(json.dumps({"screens": [screen]}), encoding="utf-8")
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={config}"
    os.environ["QT_SCALE_FACTOR"] = f"{scale:g}"
    for key in ("QT_SCREEN_SCALE_FACTORS", "QT_ENABLE_HIGHDPI_SCALING", "QT_AUTO_SCREEN_SCALE_FACTOR"):
        os.environ.pop(key, None)
    os.environ["MADHURA_NO_SOUND"] = "1"
    os.environ["MADHURA_NO_SINGLE_INSTANCE"] = "1"
    os.environ["MADHURA_DATA_DIR"] = str(root / "default-data")
    os.environ["XDG_CONFIG_HOME"] = str(root / "xdg-config")  # onboarding's launch-at-login
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


#: Offscreen-platform warnings that say nothing about Madhura.
_PLATFORM_NOISE = (
    "This plugin does not support",
    "QPlatformNativeInterface::systemTrayWindowChanged",
)


def _install_message_filter() -> None:
    from PySide6.QtCore import qInstallMessageHandler

    def handler(_kind: Any, _context: Any, message: str) -> None:
        if not any(noise in message for noise in _PLATFORM_NOISE):
            print(message, file=sys.stderr)

    qInstallMessageHandler(handler)


def _load_seed_demo() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("madhura_seed_demo", REPO_ROOT / "tools" / "seed_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =========================================================================== the shooter


class Shooter:
    """Drives one app context at a time and writes the PNGs."""

    def __init__(self, app: Any, out: Path, scale: float, only: set[str] | None, root: Path) -> None:
        self.app = app
        self.out = out
        self.scale = scale
        self.only = only
        self.root = root
        self.theme = "dark"
        self.ctx: Any = None
        self.clock: Any = None
        self.written: list[Path] = []

    # ------------------------------------------------------------------ plumbing
    def pump(self, rounds: int = 4) -> None:
        for _ in range(rounds):
            self.app.processEvents()

    def wait(self, ms: int) -> None:
        from PySide6.QtTest import QTest

        QTest.qWait(ms)

    def wanted(self, name: str) -> bool:
        return self.only is None or name in self.only

    def path(self, name: str) -> Path:
        return self.out / f"{name}_{self.theme}.png"

    def save(self, pixmap: Any, name: str) -> None:
        if not self.wanted(name):
            return
        path = self.path(name)
        if not pixmap.save(str(path)):
            raise RuntimeError(f"could not write {path}")
        self.written.append(path)

    @property
    def window(self) -> Any:
        return self.ctx.window

    def activate(self) -> None:
        """Make the main window the active window (the offscreen platform does not re-activate
        a window that was hidden and shown again, which a real window manager does)."""
        import warnings

        from PySide6.QtWidgets import QApplication

        window = self.window
        if not window.isVisible():
            window.show_main(activate=True)
        self.pump()
        if not window.isActiveWindow():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                QApplication.setActiveWindow(window)
        self.pump()

    def show_page(self, key: str) -> None:
        self.window.show_page(key)
        self.pump(6)

    def advance(self, seconds: float, step: float = 10.0, *, answer: str | None = "focused") -> None:
        """Move the FakeClock in small steps polling the engine; answer check-ins that open."""
        ctx = self.ctx
        left = float(seconds)
        while left > 1e-9:
            d = min(step, left)
            self.clock.advance(d)
            ctx.engine.poll()
            self.app.processEvents()
            left -= d
            if answer is not None and ctx.checkins is not None and ctx.checkins.prompt_mode is not None:
                ctx.checkins.answer(answer)
                self._drop_checkin_ui()

    def _drop_checkin_ui(self) -> None:
        checkins = self.ctx.checkins
        popup = getattr(checkins, "popup", None)
        if popup is not None:
            popup.dismiss(animate=False)
        strip = self.window.page("focus").active_view.checkin_strip
        if strip.phase() != "status":
            strip.cancel()
        self.pump()

    def clean_window(self) -> None:
        """No stray toast in a window shot."""
        toasts = getattr(self.window, "toasts", None)
        if toasts is not None and toasts.is_showing():
            toasts.dismiss()
        self.pump(4)

    def grab_full(self, widget: Any, name: str) -> None:
        """A whole (scrollable) page column, painted on the page background."""
        from PySide6.QtGui import QColor, QPixmap

        if not self.wanted(name):
            return
        self.pump(4)
        dpr = self.scale
        pm = QPixmap(round(widget.width() * dpr), round(widget.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(self.ctx.theme.palette.bg))
        widget.render(pm)
        self.save(pm, name)

    def grab_window(self, name: str, *, keep_toast: bool = False) -> None:
        if not keep_toast:
            self.clean_window()
        self.pump(4)
        self.save(self.window.grab(), name)

    # ------------------------------------------------------------------ compositing
    def backdrop(self, width: int, height: int) -> Any:
        """A soft desktop-like gradient (logical size; device pixels follow --scale)."""
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient

        dpr = self.scale
        pm = QPixmap(round(width * dpr), round(height * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        g = QLinearGradient(0, 0, width, height)
        if self.theme == "dark":
            g.setColorAt(0.0, QColor("#2B3040"))
            g.setColorAt(1.0, QColor("#15171D"))
            glow = QColor(122, 134, 255, 46)
        else:
            g.setColorAt(0.0, QColor("#E9EDF3"))
            g.setColorAt(1.0, QColor("#C9D1DD"))
            glow = QColor(79, 91, 213, 30)
        p.fillRect(0, 0, width, height, g)
        r = QRadialGradient(QPointF(width * 0.78, height * 0.18), max(width, height) * 0.6)
        r.setColorAt(0.0, glow)
        r.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, width, height, r)
        p.end()
        return pm

    def compose_panels(self, widgets: Sequence[Any], name: str, *, pad: int = 28, gap: int = 12) -> None:
        """Floating panels (translucent windows with their own shadow) on the backdrop."""
        from PySide6.QtGui import QPainter

        if not self.wanted(name):
            return
        self.pump(4)
        grabs = [w.grab() for w in widgets]
        width = max(round(g.width() / g.devicePixelRatio()) for g in grabs) + 2 * pad
        height = sum(round(g.height() / g.devicePixelRatio()) for g in grabs) + gap * (len(grabs) - 1) + 2 * pad
        canvas = self.backdrop(width, height)
        p = QPainter(canvas)
        y = pad
        for g in grabs:
            p.drawPixmap(pad, y, g)
            y += round(g.height() / g.devicePixelRatio()) + gap
        p.end()
        self.save(canvas, name)

    def compose_hero(self, window_shot: Any, floating: Sequence[Any]) -> None:
        """Main window + the floating panels at their real bottom-right placement."""
        from PySide6.QtCore import QPoint, QRect, QRectF
        from PySide6.QtGui import QPainter, QPainterPath

        from madhura.ui.widgets.popup import paint_shadow

        if not self.wanted("hero"):
            return
        width, height = HERO_SIZE
        canvas = self.backdrop(width, height)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        win_w, win_h = WINDOW_SIZE
        frame = QRect(64, 56, win_w, win_h)
        margin = 36
        paint_shadow(p, QRectF(frame.adjusted(-margin, -margin, margin, margin)), 12, margin, blur=36, offset_y=14,
                     alpha=0.5 if self.theme == "dark" else 0.22)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(frame), 10, 10)
        p.save()
        p.setClipPath(clip)
        p.drawPixmap(frame.topLeft(), window_shot)
        p.restore()
        offset = QPoint(width - SCREEN_SIZE[0], height - SCREEN_SIZE[1])
        for widget in floating:
            p.drawPixmap(widget.geometry().topLeft() + offset, widget.grab())
        p.end()
        self.save(canvas, "hero")

    # ------------------------------------------------------------------ contexts
    def boot(self, data_dir: Path, *, onboarded: bool = True) -> Any:
        from madhura import app as appmod
        from madhura.core.clock import FakeClock
        from madhura.data.settings import SettingsStore
        from madhura.ui.widgets.popup import PopupStack

        store = SettingsStore(data_dir / "settings.json")
        store.update(
            theme=self.theme,
            accent="indigo",
            reduce_motion=True,
            time_format="24h",
            # The renderer hides/shows the window itself; first run keeps the default ("mini").
            after_start="stay" if onboarded else "mini",
            onboarded=onboarded,
        )
        store.flush()
        store.deleteLater()
        PopupStack.reset()
        self.clock = FakeClock(NOW)
        ctx = appmod.create_context(self.app, data_dir, self.clock, tray_available=True)
        self.ctx = ctx
        window = appmod.create_main_window(ctx)
        window._quit_fn = lambda: None  # noqa: SLF001 - never quit the renderer
        appmod.start_services(ctx)
        self._freeze_background(ctx)
        window.resize(*WINDOW_SIZE)
        window.move(*WINDOW_POS)
        window.show_main(activate=True)
        self.pump(6)
        appmod.after_show(ctx)
        self.pump(6)
        return ctx

    @staticmethod
    def _freeze_background(ctx: Any) -> None:
        """Wall-clock timers must not change what is on screen between runs."""
        if ctx.habit_reminders is not None:
            ctx.habit_reminders.stop()
        if ctx.day_watcher is not None:
            ctx.day_watcher.stop()
        ctx.attention.stop()
        nudges = ctx.nudges
        if nudges is not None:
            for timer in (nudges._timer, nudges._firm_retry):  # noqa: SLF001 - renderer only
                timer.stop()
                timer.timeout.disconnect()
            nudges.check = lambda: None  # type: ignore[method-assign]

    def dispose(self) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent, Qt
        from PySide6.QtWidgets import QApplication

        from madhura import app as appmod
        from madhura.ui import theme
        from madhura.ui.widgets.popup import PopupStack

        ctx = self.ctx
        if ctx is None:
            return
        appmod.shutdown(ctx)
        PopupStack.reset()
        for w in QApplication.topLevelWidgets():
            if w.windowType() == Qt.WindowType.Popup:
                w.hide()
            elif w.parent() is None:
                w.hide()
                w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        theme.set_current(None)
        self.pump()
        self.ctx = None

    # ================================================================== scenarios
    def run_theme(self, theme_name: str, template: Path) -> None:
        self.theme = theme_name
        # Launch at login is real OS state (an XDG autostart file in the temp config dir): the
        # previous theme's onboarding must not switch it on for this one.
        shutil.rmtree(self.root / "xdg-config", ignore_errors=True)
        # The same path for every theme and run (Settings shows the data folder).
        seeded = self.root / "Madhura"
        shutil.rmtree(seeded, ignore_errors=True)
        shutil.copytree(template, seeded)
        self.boot(seeded)
        try:
            self.planning_screens()
            self.session_screens()
        finally:
            self.dispose()
        fresh = self.root / "Madhura-first-run"
        shutil.rmtree(fresh, ignore_errors=True)
        fresh.mkdir()
        self.boot(fresh, onboarded=False)
        try:
            self.first_run_screens()
        finally:
            self.dispose()

    # ------------------------------------------------------------------ planning (idle) screens
    def planning_screens(self) -> None:
        window = self.window
        self.show_page("focus")
        self.grab_window("focus_idle")
        idle = window.page("focus").idle_view
        self.grab_full(idle.column, "focus_idle_full")

        self.show_page("habits")
        self.grab_window("habits")
        habits = window.page("habits")
        editor = habits.new_habit("Work deeply on your main project")
        self.pump(8)
        self.grab_window("habit_editor")
        editor.close_sheet()
        self.pump(8)

        self.show_page("insights")
        insights = window.page("insights")
        for key in ("7d", "30d", "12w"):
            insights.set_range(key, animate=False)
            self.pump(6)
            insights.verticalScrollBar().setValue(0)
            self.grab_window(f"insights_{key}")
            if key == "7d":
                self.grab_full(insights.column, "insights_7d_full")
        insights.set_range("7d", animate=False)

        self.show_page("settings")
        settings = window.page("settings")
        settings.verticalScrollBar().setValue(0)
        self.grab_window("settings_top")
        for title, name in (("Sound", "settings_sound"), ("Appearance", "settings_appearance"), ("Data", "settings_data")):
            group = settings.groups[title]
            y = group.mapTo(settings.widget(), group.rect().topLeft()).y()
            settings.verticalScrollBar().setValue(max(0, y - 24))
            self.pump(4)
            self.grab_window(name)
        settings.verticalScrollBar().setValue(0)

        window.show_shortcuts()
        self.pump(8)
        self.grab_window("shortcuts")
        window.close_sheet()
        self.pump(8)

        from madhura.ui.overlays.wrap_up import open_wrap_up

        self.show_page("focus")
        sheet = open_wrap_up(self.ctx)
        self.pump(8)
        sheet.went_well.set_text("Two deep blocks before lunch")
        sheet.first_block.set_text("Write the release announcement")
        self.pump(6)
        self.grab_window("wrap_up")
        sheet.close_sheet()
        self.pump(8)

    # ------------------------------------------------------------------ a focus session
    def session_screens(self) -> None:
        from PySide6.QtWidgets import QApplication

        ctx = self.ctx
        window = self.window
        page = window.page("focus")
        idle, active = page.idle_view, page.active_view
        self.show_page("focus")
        self.activate()
        first = idle.up_next.first_unfinished()
        assert first is not None, "the demo data has an Up next item"
        idle.up_next.start_task(first.id)
        self.pump(8)

        # 7 min: the inline check-in on the Focus page.
        self.advance(7 * 60 - 1, answer=None)
        self.activate()
        self.advance(1, 1, answer=None)
        assert ctx.checkins.prompt_mode == "inline", ctx.checkins.prompt_mode
        self.grab_window("focus_active_checkin_inline")
        active.checkin_strip.answer_focused()
        self.wait(1300)  # the 1.2 s confirmation
        # 14 min: drifted (Phone); then a little further for the active screen.
        self.advance(7 * 60, answer=None)
        if ctx.checkins.prompt_mode is not None:
            ctx.checkins.answer("drifted", "Phone")
            self._drop_checkin_ui()
        self.advance(3 * 60 + 25, answer=None)
        self.grab_window("focus_active")

        ctx.engine.pause()
        self.pump(6)
        self.grab_window("focus_paused")
        ctx.engine.resume()
        self.pump(4)

        # Hero: the window just before the 21-minute check-in, then the pill + the check-in
        # popup once the window is hidden (so all three show the same moment).
        self.advance(21 * 60 - ctx.engine.focused_seconds - 2, answer=None)
        self.clean_window()
        hero_window = window.grab()

        # Hidden to the tray → the pill; at 21 min the popup check-in.
        window.hide_main()
        self.pump(8)
        pill_ctl = ctx.pill
        pill = pill_ctl.pill
        self.compose_panels([pill], "pill_collapsed")
        pill.set_expanded(True, animate=False)
        self.pump(4)
        self.compose_panels([pill], "pill_expanded")
        pill.set_expanded(False, animate=False)
        self.pump(4)
        self.advance(4, 1, answer=None)
        popup = ctx.checkins.popup
        assert popup is not None and ctx.checkins.prompt_mode == "popup"
        self.pump(6)
        self.compose_panels([popup], "checkin_popup")
        self.compose_hero(hero_window, [popup, pill])
        popup.answer_back()
        self.pump(8)
        self._freeze_countdown(popup._reason_countdown, popup.reason_bar)  # noqa: SLF001
        self.compose_panels([popup], "checkin_popup_reasons")
        popup.choose_reason("Messages")
        popup.dismiss(animate=False)
        self.pump(6)

        from madhura.ui.overlays.away_panel import AwayPanel

        away = AwayPanel(ctx, 14 * 60)
        away.present()
        self.pump(8)
        self._freeze_countdown(away.countdown, away.bar)
        self.compose_panels([away], "away_panel")
        away.dismiss(animate=False)
        self.pump(4)

        self.pill_states()

        # Natural finish while hidden → the completion panel (and a pending summary sheet).
        self.advance(ctx.engine.remaining_seconds + 5, 10)
        self.pump(6)
        pill_ctl._end_done()  # noqa: SLF001 - skip the 3 s "Done" display
        from madhura.ui.overlays.session_complete import CompletionPanel

        panels = [w for w in QApplication.topLevelWidgets() if isinstance(w, CompletionPanel) and w.isVisible()]
        assert panels, "no completion panel"
        self.compose_panels(panels[:1], "completion_panel")
        panels[0].dismiss(animate=False)
        self.pump(4)

        # Back to the window: the pending "Session complete" sheet.
        self.activate()
        self.pump(10)
        sheet = window.sheet_host.current()
        assert sheet is not None, "no pending summary sheet"
        sheet.note.set_text("Slides are done — the demo flow needs one more pass.")
        self.pump(6)
        self.grab_window("session_complete")
        sheet.main_button.click()  # Take a 5-min break
        self.pump(10)
        self.advance(75, 5, answer=None)
        self.grab_window("focus_break")
        ctx.engine.skip_break()
        self.pump(8)

        # A gentle nudge (built directly: the nudge timer is frozen for determinism).
        from madhura.ui.overlays.nudge_popup import NudgePopup

        window.hide_main()
        self.pump(4)
        nudge = NudgePopup(ctx, ctx.nudges, "next")
        nudge.present()
        self.pump(8)
        self.compose_panels([nudge], "nudge_popup")
        nudge.dismiss(animate=False)
        self.pump(4)

        # A habit reminder: a habit due a minute from now.
        due = (self.clock.now() + timedelta(minutes=1)).strftime("%H:%M")
        ctx.repo.add_habit("Stretch for five minutes", "🧘", "After a focus block", due)
        ctx.events.data_changed.emit("habits")
        self.pump(4)
        self.advance(150, 30, answer=None)
        ctx.habit_reminders.check()
        self.pump(8)
        reminder = ctx.habit_reminders.popup
        assert reminder is not None and reminder.isVisible(), "no habit reminder"
        self.compose_panels([reminder], "habit_reminder")

    def _freeze_countdown(self, countdown: Any, bar: Any, fraction: float = 0.8) -> None:
        """Wall-clock countdown bars (reason step, away panel): stop them at a fixed point."""
        countdown.stop()
        bar.set_fraction(fraction)
        self.pump(2)

    def pill_states(self) -> None:
        """Paused, break and done pills side by side (collapsed and expanded)."""
        if not self.wanted("pill_states"):
            return
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QPainter

        ctx = self.ctx
        pill = ctx.pill.pill
        snapshots: list[Any] = []
        ctx.engine.pause()
        ctx.pill.refresh()
        self.pump(4)
        snapshots.append(pill.grab())
        ctx.engine.resume()
        task = pill.task_text()
        for mode, collapsed, expanded, label in (
            ("break", "4m", "4:12", "Lunch"),
            ("done", "Done", "Done", task),
        ):
            pill.set_display(mode, collapsed=collapsed, expanded=expanded, task=label, fraction=0.7)
            self.pump(4)
            snapshots.append(pill.grab())
        ctx.pill.refresh()
        self.pump(4)
        pad, gap = 28, 12
        widths = [round(s.width() / s.devicePixelRatio()) for s in snapshots]
        height = max(round(s.height() / s.devicePixelRatio()) for s in snapshots)
        canvas = self.backdrop(sum(widths) + gap * (len(widths) - 1) + 2 * pad, height + 2 * pad)
        p = QPainter(canvas)
        x = pad
        for s, w in zip(snapshots, widths, strict=True):
            p.drawPixmap(QRect(x, pad, w, round(s.height() / s.devicePixelRatio())), s)
            x += w + gap
        p.end()
        self.save(canvas, "pill_states")

    # ------------------------------------------------------------------ first run + empty states
    def first_run_screens(self) -> None:
        from madhura.ui.overlays.onboarding import OnboardingSheet

        window = self.window
        sheet = window.sheet_host.current()
        assert isinstance(sheet, OnboardingSheet), "onboarding did not open"
        for step in range(4):
            if step:
                sheet.next()
            self.pump(10)
            self.grab_window(f"onboarding_{step + 1}")
        sheet.finish()
        self.pump(10)
        for key, name in (("focus", "focus_empty"), ("habits", "habits_empty"), ("insights", "insights_empty")):
            self.show_page(key)
            self.grab_window(name)


# =========================================================================== CLI


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="screenshots.py",
        description="Render deterministic Madhura screenshots (every screen and state, dark and light).",
    )
    default_out = DEFAULT_OUT.relative_to(REPO_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {default_out})")
    parser.add_argument("--theme", choices=("dark", "light", "both"), default="both", help="theme(s) to render (default: both)")
    parser.add_argument("--scale", type=float, default=1.0, help="device pixel ratio of the renders, e.g. 1.5 or 2 (default: 1)")
    parser.add_argument("--only", default=None, help="comma-separated shot names to write (default: all; see --list)")
    parser.add_argument("--list", action="store_true", help="print the shot names and exit")
    parser.add_argument("--keep-temp", action="store_true", help="keep the temporary data directories (debugging)")
    args = parser.parse_args(list(argv))
    if not 0.5 <= args.scale <= 4:
        parser.error("--scale must be between 0.5 and 4")
    if args.only:
        names = {n.strip() for n in args.only.split(",") if n.strip()}
        unknown = sorted(names - set(SHOTS))
        if unknown:
            parser.error(f"unknown shot(s): {', '.join(unknown)} (see --list)")
        args.only = names
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.list:
        print("\n".join(SHOTS))
        return 0
    # A fixed scratch path keeps the renders byte-for-byte reproducible.
    root = Path(tempfile.gettempdir()) / "madhura-screenshots"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        _prepare_environment(args.scale, root)
        from PySide6.QtWidgets import QApplication

        from madhura.core.clock import FakeClock
        from madhura.data import legacy_import
        from madhura.data.repository import Repository
        from madhura.ui import fonts
        from madhura.ui.widgets.inputs import install_wheel_guard

        legacy_import.find_legacy_habits = lambda: []  # never scan the real home directory
        _install_message_filter()
        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setStyle("Fusion")
        app.setCursorFlashTime(0)  # a steady text cursor
        fonts.apply_app_font(app)
        install_wheel_guard(app)

        template = root / "template"
        template.mkdir()
        repo = Repository(template / "madhura.db", clock=FakeClock(NOW))
        try:
            _load_seed_demo().seed_demo(repo, NOW, weeks=WEEKS, seed=SEED)
        finally:
            repo.close()

        out = args.out.expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        shooter = Shooter(app, out, args.scale, args.only, root)
        themes = THEMES if args.theme == "both" else (args.theme,)
        for theme_name in themes:
            shooter.run_theme(theme_name, template)
        for path in shooter.written:
            print(path)
        print(f"{len(shooter.written)} screenshots in {out}")
        return 0
    finally:
        if args.keep_temp:
            print(f"temp data kept in {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
