"""End-to-end journeys on the real app objects (create_context → create_main_window → tray →
start_services → show → after_show, exactly like ``madhura.app.main`` minus the event loop).

Time is driven with the FakeClock (``Journey.advance`` moves it in small steps and calls
``engine.poll()`` + processes events), the tray is forced with ``MADHURA_FORCE_TRAY=1`` and
every journey asserts that no ERROR log record, uncaught exception (``sys.excepthook``) or Qt
critical message happened — including during shutdown.
"""

from __future__ import annotations

import logging
import sys
import warnings
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from madhura.context import AppContext
from madhura.core.clock import FakeClock

#: Wednesday 14 Oct 2026, 10:00 — inside the default work hours (Mon–Fri 09:00–18:00).
MORNING = datetime(2026, 10, 14, 10, 0, 0)


# =========================================================================== error capture


class _ErrorRecorder(logging.Handler):
    """Collects ERROR (and worse) log records from every logger."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _describe(record: logging.LogRecord) -> str:
    text = f"{record.name}: {record.getMessage()}"
    if record.exc_info and record.exc_info[1] is not None:
        text += f" [{type(record.exc_info[1]).__name__}: {record.exc_info[1]}]"
    return text


# =========================================================================== the harness


class Journey:
    """One Madhura "process" on a data dir: boot, drive time, restart, shut down."""

    def __init__(self, qapp: QApplication, qtbot: Any, data_dir: Path, clock: FakeClock) -> None:
        self.qapp = qapp
        self.qtbot = qtbot
        self.data_dir = data_dir
        self.clock = clock
        self.ctx: AppContext | None = None
        self.quits = 0
        self.errors = _ErrorRecorder()
        self.exceptions: list[BaseException] = []
        self._prev_hook: Callable[..., Any] | None = None
        self._contexts: list[AppContext] = []
        logging.getLogger().addHandler(self.errors)

    # ------------------------------------------------------------------ boot / quit
    def write_settings(self, **values: Any) -> None:
        """Persist settings before boot (as a previous run would have)."""
        from madhura.data.settings import SettingsStore

        store = SettingsStore(self.data_dir / "settings.json")
        store.update(**values)
        store.flush()
        store.deleteLater()

    def boot(self, *, show: bool = True) -> AppContext:
        """``main()`` without the event loop: context, window, tray, services, show, after_show."""
        from madhura import app as appmod
        from madhura.ui.widgets.popup import PopupStack

        self._install_excepthook()
        PopupStack.reset()
        ctx = appmod.create_context(self.qapp, self.data_dir, self.clock)
        window = appmod.create_main_window(ctx)
        window._quit_fn = lambda: self._on_quit(ctx)  # noqa: SLF001 - stands in for app.quit → aboutToQuit
        if ctx.tray_available:
            appmod._create_tray(ctx)  # noqa: SLF001
        appmod.start_services(ctx)
        self.ctx = ctx
        self._contexts.append(ctx)
        if show:
            window.show_main(activate=True)
            self.wait_active()
        appmod.after_show(ctx)
        self.process()
        return ctx

    def _on_quit(self, ctx: AppContext) -> None:
        from madhura import app as appmod

        self.quits += 1
        appmod.shutdown(ctx)

    def _install_excepthook(self) -> None:
        if self._prev_hook is not None:
            return
        self._prev_hook = sys.excepthook

        def hook(exc_type: Any, exc: BaseException, tb: Any) -> None:
            self.exceptions.append(exc)
            if self._prev_hook is not None:
                self._prev_hook(exc_type, exc, tb)

        sys.excepthook = hook

    def dispose(self, ctx: AppContext | None = None) -> None:
        """Shut a context down and delete its windows (as the process exit would)."""
        from test_shell_support import release_context

        from madhura.app import shutdown
        from madhura.ui import theme
        from madhura.ui.widgets.popup import PopupStack

        ctx = ctx or self.ctx
        if ctx is None or ctx.flags.get("_disposed"):
            return
        ctx.flags["_disposed"] = True
        shutdown(ctx)
        window = ctx.window
        if window is not None:
            window._quitting = True  # noqa: SLF001
            window.hide()
            window.deleteLater()  # type: ignore[attr-defined]
        if ctx.tray is not None:
            ctx.tray.hide()
        PopupStack.reset()
        self.qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        release_context(ctx)
        theme.set_current(None)
        self.qapp.setStyleSheet("")
        self.qapp.processEvents()
        if ctx is self.ctx:
            self.ctx = None

    def restart(self, *, after_seconds: float = 0.0, show: bool = True) -> AppContext:
        """Quit-free process restart on the same data dir (the old context is disposed)."""
        self.dispose()
        if after_seconds:
            self.clock.advance(after_seconds)
        return self.boot(show=show)

    def close(self) -> None:
        for ctx in list(self._contexts):
            self.dispose(ctx)
        if self._prev_hook is not None and sys.excepthook is not self._prev_hook:
            sys.excepthook = self._prev_hook
        logging.getLogger().removeHandler(self.errors)

    # ------------------------------------------------------------------ time
    def process(self, rounds: int = 2) -> None:
        for _ in range(rounds):
            self.qapp.processEvents()

    def advance(self, seconds: float, step: float = 10.0) -> None:
        """Move the FakeClock in ≤ *step* s hops, polling the engine and processing events."""
        ctx = self.ctx
        assert ctx is not None
        remaining = float(seconds)
        while remaining > 1e-9:
            d = min(step, remaining)
            self.clock.advance(d)
            ctx.engine.poll()
            self.qapp.processEvents()
            remaining -= d

    def advance_until(self, predicate: Callable[[], bool], limit: float, step: float = 10.0) -> float:
        """Advance until *predicate* holds (at most *limit* s); returns the seconds advanced."""
        spent = 0.0
        while not predicate():
            assert spent < limit, f"condition not reached within {limit:.0f} s of FakeClock time"
            self.advance(step, step)
            spent += step
        return spent

    def wait(self, ms: int) -> None:
        self.qtbot.wait(ms)

    def settle(self, ms: int) -> None:
        """Let an animation of *ms* finish (instant with reduce-motion)."""
        from madhura.ui import animations

        if animations.motion_enabled():
            self.qtbot.wait(ms)
        else:
            self.process()

    def wait_until(self, predicate: Callable[[], bool], timeout: int = 3000) -> None:
        self.qtbot.waitUntil(predicate, timeout=timeout)

    def wait_active(self) -> None:
        """Wait for the window the app asked to activate (``show_main(activate=True)``).

        The offscreen platform never re-activates a window that was hidden and shown again (a
        real window manager does), so after a short wait emulate that activation."""
        window = self.window
        try:
            self.wait_until(window.is_main_active, 300)
        except Exception:  # noqa: BLE001 - pytestqt.TimeoutError
            assert window.is_main_visible(), "the window was not shown"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                QApplication.setActiveWindow(window)
            self.wait_until(window.is_main_active, 1000)

    # ------------------------------------------------------------------ shortcuts
    @property
    def window(self) -> Any:
        assert self.ctx is not None and self.ctx.window is not None
        return self.ctx.window

    @property
    def focus_page(self) -> Any:
        return self.window.page("focus")

    @property
    def idle(self) -> Any:
        return self.focus_page.idle_view

    @property
    def active(self) -> Any:
        return self.focus_page.active_view

    def click(self, widget: QWidget) -> None:
        assert widget.isVisible(), f"{widget!r} ({getattr(widget, 'text', lambda: '')()}) is not visible"
        assert widget.isEnabled(), f"{widget!r} is disabled"
        self._close_popup_for(widget)
        self.qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.process()

    def _close_popup_for(self, widget: QWidget) -> None:
        """A real click outside an open popup (e.g. a completion list) first closes it, and the
        window under the pointer becomes active again. QTest delivers the click directly and
        the offscreen platform keeps a hidden popup "active", so emulate both here."""
        popup = QApplication.activePopupWidget()
        if popup is not None and popup is not widget and not popup.isAncestorOf(widget):
            popup.hide()
        active = QApplication.activeWindow()
        target = widget.window()
        if active is not None and not active.isVisible() and not (target.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus):
            target.activateWindow()
            self.wait_until(target.isActiveWindow, 1000)

    def type_into(self, line_edit: Any, text: str) -> None:
        """Type *text* as key events (QTest only maps ASCII; other characters are sent as text)."""
        line_edit.setFocus(Qt.FocusReason.MouseFocusReason)
        line_edit.selectAll()
        for ch in text:
            if ch.isascii():
                self.qtbot.keyClicks(line_edit, ch)
            else:
                for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    QApplication.sendEvent(line_edit, QKeyEvent(kind, 0, Qt.KeyboardModifier.NoModifier, ch))

    def panels(self) -> list[Any]:
        from madhura.ui.widgets.popup import PopupStack

        stack = PopupStack.instance_if_exists()
        return list(stack.visible_panels()) if stack is not None else []

    def panel_kinds(self) -> list[str]:
        return [p.kind for p in self.panels()]

    def sheet(self) -> Any:
        return self.window.sheet_host.current()

    def toasts(self) -> list[str]:
        host = self.window.toasts
        return [host.text()] if host.is_showing() else []

    # ------------------------------------------------------------------ assertions
    def problems(self) -> list[str]:
        """Errors not reported yet (each is reported once)."""
        found = [_describe(r) for r in self.errors.records] + [f"uncaught {type(e).__name__}: {e}" for e in self.exceptions]
        self.errors.records.clear()
        self.exceptions.clear()
        return found

    def assert_clean(self) -> None:
        self.process()
        problems = self.problems()
        assert not problems, "errors during the journey:\n" + "\n".join(problems)


@pytest.fixture
def journey(qapp: QApplication, qtbot: Any, data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Journey]:
    monkeypatch.setenv("MADHURA_FORCE_TRAY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))  # launch-at-login writes here
    j = Journey(qapp, qtbot, data_dir, FakeClock(MORNING))
    try:
        yield j
    finally:
        j.close()
    problems = j.problems()
    assert not problems, "errors during the journey or its shutdown:\n" + "\n".join(problems)


def _settle_session(j: Journey) -> None:
    """Let the 220 ms idle↔active cross-fade and panel animations finish."""
    j.settle(260)


# =========================================================================== (a) first run


def test_first_run_onboarding_to_first_block_and_mini_timer(journey: Journey) -> None:
    from madhura.ui.overlays.onboarding import OnboardingSheet

    j = journey
    ctx = j.boot()
    assert ctx.flags["first_run"] is True
    assert ctx.tray is not None and ctx.tray_available
    window = j.window
    assert window.isVisible() and window.is_main_active()

    # The onboarding sheet is up and the rest of the window waits behind it.
    sheet = j.sheet()
    assert isinstance(sheet, OnboardingSheet)
    assert window.has_sheet()
    assert sheet.step() == 0
    for expected in (1, 2, 3):
        j.click(sheet.next_button)
        assert sheet.step() == expected
    # Step 4: first block — name it, keep 25m, Lock in.
    assert sheet.duration_chips.value() == 25
    j.type_into(sheet.task_field.line_edit, "Write the launch post")
    j.click(sheet.lock_in_button)

    assert ctx.settings.get("onboarded") is True
    assert str(ctx.engine.state) == "focus"
    sid = ctx.engine.session_id
    session = ctx.repo.get_session(sid)
    assert session is not None and session.task == "Write the launch post" and session.planned_seconds == 25 * 60
    j.wait_until(lambda: not window.has_sheet())
    # Launch at login defaulted on (Linux: an XDG autostart entry in the temp config dir).
    from madhura.platform import autostart

    if autostart.is_supported():
        assert autostart.is_enabled()

    # after_start "mini": ~900 ms later the window hides to the tray and the pill shows.
    assert ctx.settings.get("after_start") == "mini"
    j.wait_until(lambda: not window.isVisible(), 3000)
    assert not window.is_main_visible()
    assert ctx.pill is not None
    j.wait_until(lambda: ctx.pill.is_showing() and ctx.pill.pill.isVisible())
    assert ctx.pill.pill.time_text() == "25m"
    ctx.tray.refresh_menu()  # aboutToShow
    assert ctx.tray.action("pill").text() == "Mini timer: Hide"
    j.advance(60)
    assert ctx.pill.pill.time_text() == "24m"
    j.assert_clean()


# =========================================================================== (b) full session


def _click_chip(j: Journey, group: Any, value: Any) -> None:
    """Click the chip for *value* in a painted ChipGroup."""
    rect = group.chip_rect(value)
    assert rect.isValid(), f"no chip for {value!r}"
    j.qtbot.mouseClick(group, Qt.MouseButton.LeftButton, pos=rect.center())
    j.process()


def _records(ctx: AppContext, sid: int) -> list[tuple[str, str | None]]:
    return [(str(c.response), c.reason) for c in ctx.repo.checkins_for_session(sid)]


def _lock_in(j: Journey, task: str, minutes: int) -> int:
    plan = j.idle.plan_card
    j.type_into(plan.task_field.line_edit, task)
    _click_chip(j, plan.duration, minutes)
    j.click(plan.lock_in)
    ctx = j.ctx
    assert ctx is not None and str(ctx.engine.state) == "focus"
    assert ctx.engine.task == task and ctx.engine.planned_seconds == minutes * 60
    return int(ctx.engine.session_id)


def test_full_session_hidden_window_checkins_panel_break_and_pending_sheet(journey: Journey) -> None:
    from madhura.ui.overlays.session_complete import PENDING_KEY, CompletionPanel, SessionCompleteSheet

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True)
    ctx = j.boot()
    window = j.window
    assert not window.has_sheet()  # onboarded: no onboarding sheet
    upnext_id = j.idle.up_next.add_task("Review pull requests", 45)
    assert upnext_id is not None

    sid = _lock_in(j, "Draft chapter 3", 25)
    _settle_session(j)
    assert not j.focus_page.is_idle_shown()
    # after_start "mini" → hidden to the tray, the pill takes over.
    j.wait_until(lambda: not window.isVisible())
    assert ctx.pill.is_showing()

    # 1st check-in at 7 focused minutes, window hidden → popup above the pill.
    j.advance_until(lambda: ctx.checkins.prompt_mode is not None, 8 * 60)
    assert 7 * 60 <= ctx.engine.focused_seconds < 7 * 60 + 15
    assert ctx.checkins.prompt_mode == "popup"
    popup = ctx.checkins.popup
    assert popup is not None and "checkin" in j.panel_kinds()
    assert popup.title.text() == "Still on Draft chapter 3?"
    card = popup.visual_geometry()
    assert not card.intersects(ctx.pill.pill.visual_geometry()), "check-in panel overlaps the pill"
    j.click(popup.focused_button)
    assert _records(ctx, sid) == [("focused", None)]
    assert popup.phase() == "confirm" and "next check-in" in popup.confirmation()

    # Double-click the pill → the window comes back, active on Focus; the pill hides.
    j.qtbot.mouseDClick(ctx.pill.pill, Qt.MouseButton.LeftButton)
    j.wait_active()
    assert window.current_page() == "focus"
    j.wait_until(lambda: not ctx.pill.is_showing())

    # 2nd check-in (14 min) is inline on the Focus page: Focusing back → Phone.
    strip = j.active.checkin_strip
    j.advance_until(lambda: ctx.checkins.prompt_mode is not None, 8 * 60)
    assert ctx.checkins.prompt_mode == "inline"
    assert strip.phase() == "prompt"
    j.click(strip.back_button)
    assert strip.phase() == "reason"
    phone = next(c for c in strip.reason_chips if c.text() == "Phone")
    j.click(phone)
    assert _records(ctx, sid) == [("focused", None), ("drifted", "Phone")]

    # Ctrl+H hides to the mini timer again.
    j.qtbot.keyClick(window, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
    j.process()
    assert not window.isVisible() and ctx.pill.is_showing()

    # 3rd check-in (21 min, drift reset the interval) → popup → Focusing back → Messages.
    j.advance_until(lambda: ctx.checkins.prompt_mode is not None, 8 * 60)
    assert ctx.checkins.prompt_mode == "popup"
    popup = ctx.checkins.popup
    j.click(popup.back_button)
    assert popup.phase() == "reason"
    _click_chip(j, popup.reasons, "Messages")
    assert _records(ctx, sid)[-1] == ("drifted", "Messages")
    assert popup.confirmation().startswith("Back to")

    # Natural finish at 25 min with the window hidden → completion panel + pending sheet.
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 5 * 60)
    session = ctx.repo.get_session(sid)
    assert str(session.outcome) == "completed" and session.focused_seconds == 25 * 60
    assert ctx.repo.kv_get(PENDING_KEY) == sid
    panels = [p for p in j.panels() if isinstance(p, CompletionPanel)]
    assert len(panels) == 1
    panel = panels[0]
    assert panel.title_label.text().startswith("Nice — 25 min on Draft chapter 3")
    assert "1 of 3 focused" in panel.score_label.text()
    assert ctx.pill.is_done_showing()
    assert not window.isVisible()

    # Take a 5-min break from the panel; the pill shows the break.
    assert panel.main_button.text() == "Take a 5-min break"
    j.click(panel.main_button)
    assert str(ctx.engine.state) == "break" and ctx.engine.break_total_seconds == 5 * 60
    j.wait(50)
    assert ctx.pill.is_showing() and ctx.pill.pill.mode() == "break"

    # Break over while hidden → notification; the idle view is prefilled from Up next.
    ctx.notifier.sent.clear()
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 6 * 60)
    assert ("Break's over", "Plan your next block.") in ctx.notifier.sent
    _settle_session(j)
    assert j.focus_page.is_idle_shown()
    plan = j.idle.plan_card
    assert plan.task_field.text() == "Review pull requests"
    assert plan.minutes() == 45 and plan.planned_task_id() == upnext_id
    assert not ctx.pill.is_showing()

    # Opening Madhura from the tray shows the pending summary sheet; the note is saved on Done.
    ctx.tray.toggle_window()
    j.wait_active()
    j.wait_until(lambda: isinstance(j.sheet(), SessionCompleteSheet))
    sheet = j.sheet()
    assert sheet.session_id == sid and sheet.pending
    assert sheet.title_text() == "Session complete"
    # The break was taken from the panel: the late sheet doesn't offer it (or +15) again.
    assert not sheet.main_button.isVisible() and not sheet.keep_button.isVisible()
    assert sheet.primary_button() is sheet.done_button
    sheet.note.set_text("Good flow after the phone break.")
    j.click(sheet.done_button)
    j.wait_until(lambda: not window.has_sheet())
    assert ctx.repo.get_session(sid).note == "Good flow after the phone break."
    assert ctx.repo.kv_get(PENDING_KEY) is None
    # Today card lists the session with its score.
    rows = j.idle.today_card.rows()
    assert len(rows) == 1
    j.assert_clean()


def test_full_session_active_window_inline_checkins_sheet_break_and_prefill(journey: Journey) -> None:
    from madhura.ui.overlays.session_complete import PENDING_KEY, SessionCompleteSheet

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay")
    ctx = j.boot()
    window = j.window
    upnext_id = j.idle.up_next.add_task("Outline the literature review", 60)

    sid = _lock_in(j, "Clean the survey dataset", 25)
    _settle_session(j)
    assert not j.focus_page._mini_timer.isActive()  # noqa: SLF001 - "stay": no hide to the mini timer
    assert window.is_main_active() and not ctx.pill.is_showing()

    # Every check-in is inline on the Focus page; answer with the F / B keys.
    strip = j.active.checkin_strip
    for key, expected in ((Qt.Key.Key_F, ("focused", None)),) * 3:  # at 7, 14 and 21 min
        j.advance_until(lambda: ctx.checkins.prompt_mode is not None, 12 * 60)
        assert ctx.checkins.prompt_mode == "inline" and strip.phase() == "prompt"
        j.qtbot.keyClick(window, key)
        j.process()
        assert _records(ctx, sid)[-1] == expected
    assert ctx.checkins.planner.streak == 3

    # Natural finish with the window active → the in-window summary sheet (no panel).
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 5 * 60)
    j.wait_until(lambda: isinstance(j.sheet(), SessionCompleteSheet))
    sheet = j.sheet()
    assert "completion" not in j.panel_kinds()
    assert ctx.repo.kv_get(PENDING_KEY) == sid  # until it's closed: a quit brings it back
    assert sheet.title_text() == "Session complete"
    assert sheet.summary_label.text().startswith("25 of 25 min · 10:00–10:25")
    assert sheet.score_label is not None and sheet.score_label.text() == "3 of 3 focused"
    assert sheet.mark_done is None  # not an Up next session
    assert sheet.main_button.text() == "Take a 5-min break"
    _click_chip(j, sheet.break_chips, 10)
    assert sheet.main_button.text() == "Take a 10-min break"
    j.click(sheet.main_button)
    j.wait_until(lambda: not window.has_sheet())
    assert ctx.repo.kv_get(PENDING_KEY) is None
    assert str(ctx.engine.state) == "break" and ctx.engine.break_total_seconds == 10 * 60
    _settle_session(j)
    assert j.active.mode() == "break"
    assert j.active.next_field.text() == "Outline the literature review"

    # Break over with the window active: no notification, plan card prefilled and focused.
    ctx.notifier.sent.clear()
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 11 * 60)
    _settle_session(j)
    assert ctx.notifier.sent == []
    assert j.focus_page.is_idle_shown()
    plan = j.idle.plan_card
    assert plan.task_field.text() == "Outline the literature review"
    assert plan.minutes() == 60 and plan.planned_task_id() == upnext_id
    j.wait_until(lambda: plan.task_field.line_edit.hasFocus())
    assert plan.task_field.line_edit.selectedText() == "Outline the literature review"
    j.assert_clean()


# =========================================================================== (c) Up next → done


def test_up_next_item_start_complete_and_mark_done(journey: Journey) -> None:
    from madhura.ui.overlays.session_complete import SessionCompleteSheet

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False)
    ctx = j.boot()
    up_next = j.idle.up_next

    # Add two tasks through the inline add row (Enter adds and keeps focus).
    field = up_next.add_field.line_edit
    j.type_into(field, "Polish the insights charts")
    j.qtbot.keyClick(field, Qt.Key.Key_Return)
    assert field.hasFocus() and field.text() == ""
    j.type_into(field, "Write release notes")
    j.qtbot.keyClick(field, Qt.Key.Key_Return)
    tasks = ctx.repo.tasks_for_date(ctx.today())
    assert [t.title for t in tasks] == ["Polish the insights charts", "Write release notes"]
    first = tasks[0]
    assert first.minutes == 25 and up_next.count_label.text() == "2"

    # Hover the row, click ▶.
    row = up_next.row_for(first.id)
    j.qtbot.mouseMove(row, row.rect().center())
    j.wait_until(lambda: row.start_button.isVisible(), 1000)
    j.click(row.start_button)
    assert str(ctx.engine.state) == "focus" and ctx.engine.planned_task_id == first.id
    sid = int(ctx.engine.session_id)

    j.advance_until(lambda: str(ctx.engine.state) == "idle", 26 * 60, step=30)
    j.wait_until(lambda: isinstance(j.sheet(), SessionCompleteSheet))
    sheet = j.sheet()
    assert sheet.mark_done is not None and sheet.mark_done.isChecked()  # completed → checked
    assert "Polish the insights charts" in sheet.mark_done.text()
    j.click(sheet.done_button)
    j.wait_until(lambda: not j.window.has_sheet())

    task = ctx.repo.get_task(first.id)
    assert task.done and task.completed_at is not None
    assert ctx.repo.get_session(sid).planned_task_id == first.id
    _settle_session(j)
    row = up_next.row_for(first.id)
    assert row is not None and row.check.isChecked()
    assert up_next.count_label.text() == "1"
    assert j.idle.plan_card.task_field.text() == "Write release notes"
    today_rows = j.idle.today_card.rows()
    assert len(today_rows) == 1
    j.assert_clean()


def test_quit_with_the_summary_sheet_open_keeps_the_note_and_asks_again(journey: Journey) -> None:
    """Ctrl+Q (or shutting the PC down) right after a block, with the summary sheet still up:
    the note survives, and the sheet comes back next launch for "Mark done" to be confirmed."""
    from madhura.ui.overlays.session_complete import PENDING_KEY, SessionCompleteSheet

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False)
    ctx = j.boot()
    tid = j.idle.up_next.add_task("Write report", 25)
    assert tid is not None
    sid = j.idle.up_next.start_task(tid)
    assert sid is not None
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 26 * 60, step=30)
    j.wait_until(lambda: isinstance(j.sheet(), SessionCompleteSheet))
    sheet = j.sheet()
    assert sheet.mark_done is not None and sheet.mark_done.isChecked()
    sheet.note.setFocus()
    j.qtbot.keyClicks(sheet.note, "Numbers need a second look")
    j.qtbot.keyClick(j.window, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)  # quit
    assert j.quits == 1 and ctx.repo.closed

    # Next morning: the summary is back with the note; nothing was marked done behind the back.
    ctx = j.restart(after_seconds=14 * 3600)
    j.wait_until(lambda: isinstance(j.sheet(), SessionCompleteSheet))
    sheet = j.sheet()
    assert sheet.session_id == sid and sheet.pending
    assert sheet.note.text() == "Numbers need a second look"
    assert ctx.repo.get_session(sid).note == "Numbers need a second look"
    assert not ctx.repo.get_task(tid).done
    assert sheet.mark_done is not None and sheet.mark_done.isChecked()
    assert not sheet.main_button.isVisible()  # no "Take a break" for yesterday's block
    j.click(sheet.done_button)
    j.wait_until(lambda: not j.window.has_sheet())
    assert ctx.repo.get_task(tid).done
    assert ctx.repo.kv_get(PENDING_KEY) is None
    j.assert_clean()


# =========================================================================== (d) habits


def _create_habit(j: Journey, name: str, at: str) -> int:
    from madhura.ui.pages.habit_editor import HabitEditorSheet

    j.qtbot.keyClick(j.window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)  # Ctrl+N on Habits
    j.wait_until(lambda: isinstance(j.sheet(), HabitEditorSheet))
    editor = j.sheet()
    j.type_into(editor.name_field.line_edit, name)
    j.click(editor.time_toggle)
    editor.time_field.set_time_str(at)
    assert editor.reminder_toggle.isEnabled() and editor.reminder_toggle.isChecked()
    j.click(editor.save_button)
    j.wait_until(lambda: not j.window.has_sheet())
    habit = next(h for h in j.ctx.repo.habits() if h.name == name)
    assert habit.time == at and habit.reminder and habit.days == 127
    return int(habit.id)


def _reminder_panel(j: Journey) -> Any:
    return next((p for p in j.panels() if p.kind == "habit"), None)


def test_habit_reminders_popup_hold_during_focus_and_done(journey: Journey) -> None:
    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, checkin_enabled=False)
    ctx = j.boot()
    window = j.window
    j.qtbot.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    assert window.current_page() == "habits"
    page = window.page("habits")
    assert page.is_empty()
    stretch = _create_habit(j, "Stretch", "10:05")
    water = _create_habit(j, "Drink water", "10:20")
    assert not page.is_empty() and {r.habit_id for r in page.rows()} == {stretch, water}
    reminders = ctx.habit_reminders

    # 10:05 — Stretch is due: the reminder panel appears.
    j.advance(5 * 60, 30)
    reminders.check()
    j.process()
    panel = _reminder_panel(j)
    assert panel is not None and [i.habit_id for i in panel.items()] == [stretch]
    assert panel.caption_text() == "Daily habit"
    row = panel.rows()[0]
    assert row.status_label.text() == "Due now"
    j.click(row.done_button)
    log = {lg.habit_id: str(lg.status) for lg in ctx.repo.habit_logs_for_date(ctx.today())}
    assert log == {stretch: "done"}
    assert page.model(stretch).state == "done"

    # 10:06 — start a 25 min block; Drink water comes due at 10:20 but is held during FOCUS.
    j.advance(60)
    j.qtbot.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    _lock_in(j, "Write the methods section", 25)
    j.advance(14 * 60, 30)
    reminders.check()
    j.process()
    assert reminders.queued_count == 1 and reminders.held_ids() == [water]
    assert _reminder_panel(j) is None or not _reminder_panel(j).items()

    # The session ends (10:31) → the held reminder is delivered.
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 12 * 60, 30)
    j.process()
    assert reminders.queued_count == 0
    panel = _reminder_panel(j)
    assert panel is not None and [i.habit_id for i in panel.items()] == [water]
    assert panel.rows()[0].status_label.text() == "11 min late"
    j.click(panel.rows()[0].done_button)
    log = {lg.habit_id: str(lg.status) for lg in ctx.repo.habit_logs_for_date(ctx.today())}
    assert log == {stretch: "done", water: "done"}

    # The Habits page shows both done (summary "2 of 2").
    window.show_main("habits", activate=True)
    j.wait_active()
    page.on_shown()
    assert page.model(water).state == "done" and page.model(stretch).state == "done"
    assert page.summary.done_label.text() == "2"
    j.assert_clean()


# =========================================================================== (e) nudges


def _nudge_panel(j: Journey) -> Any:
    return next((p for p in j.panels() if p.kind == "nudge"), None)


def _minutes_of_checks(j: Journey, minutes: int) -> list[Any]:
    """Advance minute by minute running the nudge check; returns the nudge panels seen."""
    seen = []
    for _ in range(minutes):
        j.advance(60, 30)
        j.ctx.nudges.check()
        j.process()
        panel = _nudge_panel(j)
        if panel is not None and panel not in seen:
            seen.append(panel)
    return seen


def test_nudges_gentle_snooze_then_firm_banner(journey: Journey) -> None:
    from madhura.services.nudge_service import DONE_KEY, SNOOZE_KEY

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True)
    ctx = j.boot()
    window = j.window
    window.hide_main()  # working in another app, Madhura in the tray
    j.process()
    assert not window.isVisible()

    # Within the 5 min idle grace: nothing. After it: the gentle panel.
    assert _minutes_of_checks(j, 4) == []
    seen = _minutes_of_checks(j, 1)
    assert len(seen) == 1
    panel = seen[0]
    assert panel.variant == "next" and panel.title.text() == "What's next?"
    assert panel.primary_text() == "Plan now"
    assert not window.isVisible()  # gentle never raises the window

    # Snooze ▾ → 30 min.
    menu = panel.snooze_menu()
    next(a for a in menu.actions() if a.text() == "30 min").trigger()
    j.process()
    assert ctx.nudges.snoozed_until == MORNING + timedelta(minutes=35)
    assert ctx.repo.kv_get(SNOOZE_KEY) == "2026-10-14T10:35:00"
    j.wait_until(lambda: _nudge_panel(j) is None)
    assert _minutes_of_checks(j, 29) == []  # 10:05 → 10:34: quiet
    seen = _minutes_of_checks(j, 2)  # the snooze ends at 10:35
    assert len(seen) == 1 and ctx.nudges.snoozed_until is None
    j.click(seen[0].close_button)  # ✕ — no penalty
    j.wait_until(lambda: _nudge_panel(j) is None)

    # Firm mode: the window is raised (without activation) with the Focus page banner.
    ctx.settings.update(nudge_mode="firm")
    _minutes_of_checks(j, 6)
    assert window.isVisible() and window.current_page() == "focus"
    assert ctx.nudges.banner_visible
    banner = j.idle.nudge_banner
    assert banner.isVisible() and "between blocks" in banner.text()
    labels = [b.text() for b in banner.action_buttons()]
    assert labels[0] == "Plan now" and "Done for today" in labels
    assert _nudge_panel(j) is None  # firm uses the banner, not the popup

    # "Done for today" stops nudging for the logical day.
    done = next(b for b in banner.action_buttons() if b.text() == "Done for today")
    j.click(done)
    assert ctx.repo.kv_get(DONE_KEY) == "2026-10-14"
    assert not banner.isVisible() and not ctx.nudges.banner_visible
    window.hide_main()
    j.process()
    assert _minutes_of_checks(j, 15) == [] and not window.isVisible()
    assert "Done for today" in j.idle.nudge_status.text_label.text()
    j.assert_clean()


# =========================================================================== (f) sleep / away


def test_suspend_gap_pauses_shows_away_banner_and_finish_saves_partial(journey: Journey) -> None:
    from madhura.ui.overlays.session_complete import SessionCompleteSheet

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False)
    ctx = j.boot()
    sid = _lock_in(j, "Quarterly report — revenue section", 45)
    j.advance(12 * 60, 30)
    assert str(ctx.engine.state) == "focus"

    # The laptop sleeps for 2 h: one poll sees the whole gap.
    away: list[int] = []
    ctx.engine.away_detected.connect(away.append)
    j.clock.advance(2 * 3600)
    ctx.engine.poll()
    j.process()
    assert str(ctx.engine.state) == "paused"
    assert away and away[0] >= 2 * 3600
    assert 12 * 60 <= ctx.engine.focused_seconds < 12 * 60 + 31  # sleep never credited
    page = j.focus_page
    assert page.away_banner_visible()
    assert page.away_banner.text().startswith("You were away 2h")
    labels = [b.text() for b in page.away_banner.action_buttons()]
    assert labels == ["Resume", "Finish (save 12 min)", "Abandon"]
    assert j.active.pause_button.text() == "Resume"

    # Finish saves the partial block; the summary sheet follows.
    j.click(page.away_banner.action_buttons()[1])
    session = ctx.repo.get_session(sid)
    assert str(session.outcome) == "ended_early"
    assert 12 * 60 <= session.focused_seconds < 12 * 60 + 31
    assert str(ctx.engine.state) == "idle"
    j.wait_until(lambda: isinstance(j.sheet(), SessionCompleteSheet))
    assert j.sheet().title_text() == "Session ended"
    assert j.sheet().summary_label.text().startswith("12 of 45 min")
    j.click(j.sheet().done_button)
    _settle_session(j)
    assert not page.away_banner_visible()
    assert j.focus_page.is_idle_shown()
    today = j.idle.today_card
    assert len(today.rows()) == 1
    j.assert_clean()


# =========================================================================== (g) quit + restore


def test_quit_mid_session_then_restore_paused_on_next_launch(journey: Journey) -> None:
    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False)
    ctx = j.boot()
    sid = _lock_in(j, "Refactor the session repository", 45)
    j.advance(10 * 60, 30)

    # Ctrl+Q: quit_app pauses the running session, then aboutToQuit → shutdown (checkpoint).
    j.qtbot.keyClick(j.window, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
    assert j.quits == 1
    assert str(ctx.engine.state) == "paused"
    assert ctx.flags.get("shut_down") and ctx.repo.closed
    old_focused = 10 * 60

    # Next launch 20 minutes later on the same data dir: the session is restored PAUSED.
    ctx2 = j.restart(after_seconds=20 * 60)
    assert ctx2 is not ctx
    engine = ctx2.engine
    assert str(engine.state) == "paused"
    assert engine.session_id == sid and engine.task == "Refactor the session repository"
    assert abs(engine.focused_seconds - old_focused) < 31
    assert engine.planned_seconds == 45 * 60
    _settle_session(j)
    page = j.focus_page
    assert not page.is_idle_shown()
    assert j.active.pause_button.text() == "Resume"
    assert j.active.ring.caption() == "Paused" and j.active.ring.text() == "35:00"
    # Resume continues the same session to completion.
    j.click(j.active.pause_button)
    assert str(engine.state) == "focus"
    j.advance_until(lambda: str(engine.state) == "idle", 36 * 60, 30)
    session = ctx2.repo.get_session(sid)
    assert str(session.outcome) == "completed" and session.focused_seconds == 45 * 60
    assert session.paused_seconds >= 20 * 60
    j.assert_clean()


# =========================================================================== (h) insights + wrap-up


def _seed(j: Journey, now: datetime = MORNING, weeks: int = 8) -> dict[str, int]:
    from test_i_support import load_seed_demo

    from madhura.data.repository import Repository

    repo = Repository(j.data_dir / "madhura.db", clock=FakeClock(now))
    try:
        return load_seed_demo().seed_demo(repo, now, weeks=weeks)
    finally:
        repo.close()


def test_insights_render_all_ranges_on_demo_data_then_wrap_up(journey: Journey) -> None:
    from madhura.services.nudge_service import DONE_KEY
    from madhura.ui.overlays.wrap_up import WrapUpSheet

    j = journey
    counts = _seed(j)
    assert counts["sessions"] > 100
    j.write_settings(onboarded=True, reduce_motion=True)
    ctx = j.boot()
    window = j.window
    # The idle view shows the seeded day: Up next, Today, parking lot, streak.
    assert j.idle.up_next.tasks() and j.idle.parking_lot.rows()
    assert j.idle.parking_lot.isVisible()

    j.qtbot.keyClick(window, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    page = window.page("insights")
    assert window.current_page() == "insights" and not page.is_empty_state()
    for key in ("7d", "30d", "12w"):
        page.set_range(key, animate=False)
        j.process()
        assert page.current_range() == key
        assert page.kpi_focus.value_text() not in ("", "—")
        assert page.kpi_sessions.value_text() not in ("", "0")
        assert page.recent_list.rows
        # Hover the last bar and a heatmap cell so the painted callouts render too.
        values = [b.value for b in page.chart.bars()]
        best = values.index(max(values))
        assert max(values) > 0 and page.chart.bar_rect(best).isValid()
        j.qtbot.mouseMove(page.chart, page.chart.bar_rect(best).center().toPoint())
        j.process()
        assert page.chart.hover_index == best and page.chart.callout_text()
        page.grab()
        page.heatmap.set_hover_cell((2, 10))
        assert page.heatmap.callout_text()
        page.heatmap.grab()
    assert page.drift.reasons.isVisible()

    # Wrap up the day from the Focus page footer → Shutdown complete → done for today.
    j.qtbot.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    j.click(j.idle.wrap_up_link)
    j.wait_until(lambda: isinstance(j.sheet(), WrapUpSheet))
    sheet = j.sheet()
    assert sheet.task_rows  # unfinished Up next items are offered
    j.type_into(sheet.went_well.line_edit, "Two deep blocks before lunch")
    j.type_into(sheet.first_block.line_edit, "Draft the intro")
    j.click(sheet.done_button)
    j.wait_until(lambda: not window.has_sheet())
    assert ctx.repo.kv_get(DONE_KEY) == "2026-10-14"
    assert ctx.nudges.is_done_for_today
    assert ctx.repo.get_day_note(date(2026, 10, 14)) == "Two deep blocks before lunch"
    thursday = ctx.repo.tasks_for_date(date(2026, 10, 15))
    assert thursday[0].title == "Draft the intro"
    assert any("Shutdown complete" in t for t in j.toasts())

    # Next morning: the first nudge names the first block.
    window.hide_main()
    j.clock.set(datetime(2026, 10, 15, 9, 30))
    ctx.engine.poll()
    assert ctx.day_watcher.check()  # day_changed
    ctx.nudges.check()
    j.process()
    panel = next((p for p in j.panels() if p.kind == "nudge"), None)
    assert panel is not None and panel.variant == "morning"
    assert panel.title.text() == "Good morning — your first block: Draft the intro"
    j.assert_clean()


# =========================================================================== (i) settings


def _click_segment(j: Journey, control: Any, value: Any) -> None:
    values = [v for v, _label in control._options]  # noqa: SLF001
    rect = control._segment_rects()[values.index(value)]  # noqa: SLF001
    j.qtbot.mouseClick(control, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    j.process()


def _visit_all_pages_and_check_theme(j: Journey) -> None:
    from PySide6.QtGui import QColor

    ctx = j.ctx
    pal = ctx.theme.palette
    app_pal = QApplication.palette()
    assert app_pal.color(QPalette.ColorRole.Window).name() == QColor(pal.bg).name()
    assert app_pal.color(QPalette.ColorRole.WindowText).name() == QColor(pal.text).name()
    window = j.window
    for key in ("focus", "habits", "insights", "settings"):
        window.show_page(key)
        j.settle(180)  # page cross-fade
        page = window.page(key)
        image = window.grab().toImage()
        # The page background (top right, left of the 10 px scrollbar gutter) is the bg token.
        corner = image.pixelColor(image.width() - 20, 6)
        assert corner.name() == QColor(pal.bg).name(), f"{key}: {corner.name()} != {pal.bg} ({ctx.theme.scheme})"
        sidebar = image.pixelColor(4, image.height() // 2)
        assert sidebar.name() == QColor(pal.sidebar).name(), f"sidebar {sidebar.name()} != {pal.sidebar}"
        page.grab()


def test_settings_reset_all_data_and_theme_switch_across_pages(journey: Journey) -> None:
    from madhura.ui.widgets.sheet import ConfirmSheet

    j = journey
    _seed(j, weeks=2)
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False)
    ctx = j.boot()
    window = j.window
    assert ctx.repo.habits() and ctx.repo.open_captures()
    _lock_in(j, "Something to throw away", 25)
    j.advance(3 * 60)

    # Theme: dark → light → system, every page visited each time.
    j.qtbot.keyClick(window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    assert window.current_page() == "settings"
    settings_page = window.page("settings")
    assert ctx.theme.scheme == "dark"
    _visit_all_pages_and_check_theme(j)
    for theme_value in ("light", "system", "dark"):
        window.show_page("settings")
        j.settle(180)
        _click_segment(j, settings_page.controls["theme"], theme_value)
        assert ctx.settings.get("theme") == theme_value
        if theme_value != "system":
            assert ctx.theme.scheme == theme_value
        _visit_all_pages_and_check_theme(j)

    # Reset all data (type "reset" to confirm) while a session runs.
    window.show_page("settings")
    j.settle(180)
    j.click(settings_page.reset_button)
    j.wait_until(lambda: isinstance(j.sheet(), ConfirmSheet))
    sheet = j.sheet()
    assert not sheet.ok_button.isEnabled()
    j.type_into(sheet.confirm_field.line_edit, "reset")
    assert sheet.ok_button.isEnabled()
    j.click(sheet.ok_button)
    j.wait_until(lambda: not window.has_sheet())

    assert str(ctx.engine.state) == "idle"
    repo = ctx.repo
    assert repo.recent_sessions(limit=5) == [] and repo.habits(include_archived=True) == []
    assert repo.open_captures() == [] and repo.tasks_for_date(ctx.today()) == []
    assert repo.kv_get("engine_snapshot") is None
    assert list((j.data_dir / "backups").glob("madhura-pre-reset*.db"))
    assert ctx.settings.get("onboarded") is True  # settings survive a data reset
    assert any("reset" in t.lower() for t in j.toasts())

    # Every page reflects the empty database.
    window.show_page("focus")
    j.settle(260)
    assert j.focus_page.is_idle_shown()
    assert j.idle.up_next.tasks() == [] and j.idle.today_card.rows() == []
    assert not j.idle.parking_lot.isVisible()
    window.show_page("habits")
    j.settle(180)
    assert window.page("habits").is_empty()
    window.show_page("insights")
    j.settle(180)
    assert window.page("insights").is_empty_state()
    j.assert_clean()


# =========================================================================== (j) day rollover


def test_day_rollover_at_day_start_hour_resets_today_and_habits(journey: Journey) -> None:
    j = journey
    j.clock.set(datetime(2026, 10, 14, 22, 0))  # Wednesday evening
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False, nudge_mode="off")
    ctx = j.boot()
    window = j.window
    assert ctx.day_start_hour() == 4 and ctx.today() == date(2026, 10, 14)
    habit_id = ctx.repo.add_habit("Read 10 pages", emoji="📖")
    ctx.events.data_changed.emit("habits")
    j.idle.up_next.add_task("Plan tomorrow", 25)

    # A 45-minute block (22:00–22:45) and the habit done, both on Wednesday.
    _lock_in(j, "Late-night reading notes", 45)
    j.advance_until(lambda: str(ctx.engine.state) == "idle", 46 * 60, 30)
    j.wait_until(lambda: window.has_sheet())
    j.click(j.sheet().done_button)
    j.wait_until(lambda: not window.has_sheet())
    window.show_page("habits")
    j.settle(180)
    habits = window.page("habits")
    habits.toggle_done(habit_id)
    assert habits.model(habit_id).state == "done"
    window.show_page("focus")
    j.settle(260)
    idle = j.idle
    assert len(idle.today_card.rows()) == 1
    assert idle.header.goal_total.text() == "45m"
    assert idle.header.date_label.text().startswith("Wednesday")

    # Midnight is still Wednesday (day starts at 04:00): no day change.
    j.advance(2 * 3600 + 15 * 60, 60)  # → 00:47
    assert not ctx.day_watcher.check()
    assert ctx.today() == date(2026, 10, 14)
    assert len(idle.today_card.rows()) == 1

    # 04:01 Thursday: day_changed → Today, goal, date, habits and carry-over all roll.
    seen: list[bool] = []
    ctx.events.day_changed.connect(lambda: seen.append(True))
    j.advance(3 * 3600 + 14 * 60, 60)
    assert ctx.day_watcher.check() and seen == [True]
    assert ctx.today() == date(2026, 10, 15)
    j.process()
    assert idle.header.date_label.text().startswith("Thursday")
    assert idle.today_card.rows() == [] and idle.today_card.empty_label.isVisible()
    assert idle.header.goal_total.text() == "0m"
    assert idle.carryover_banner.isVisible() and idle.carryover_banner.text().startswith("1 unfinished")
    assert idle.up_next.tasks() == []
    window.show_page("habits")
    j.settle(180)
    model = habits.model(habit_id)
    assert model.state == "none" and model.streak == 1  # yesterday's done keeps the streak alive
    assert habits.summary.done_label.text() == "0"
    assert list((j.data_dir / "backups").glob("madhura-2026-10-15.db"))  # the new day's backup
    j.assert_clean()


# =========================================================================== extra: tray, away, wrap-up prompt


def test_tray_menu_drives_a_session_quiet_time_and_wrap_up(journey: Journey) -> None:
    from madhura.ui.overlays.wrap_up import WrapUpSheet

    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, checkin_enabled=False)
    ctx = j.boot()
    window = j.window
    tray = ctx.tray
    window.hide_main()
    j.process()

    def act(name: str) -> None:
        tray.refresh_menu()  # aboutToShow
        action = tray.action(name)
        assert action.isEnabled(), f"tray action {name!r} is disabled"
        action.trigger()
        j.process()

    # Start focus… opens the window on Focus with the task field focused.
    act("start")
    j.wait_active()
    assert window.current_page() == "focus"
    j.wait_until(lambda: j.idle.plan_card.task_field.line_edit.hasFocus())
    _lock_in(j, "Answer the onboarding questions", 25)
    j.wait_until(lambda: not window.isVisible())  # mini timer
    j.advance(5 * 60)

    act("pause")
    assert str(ctx.engine.state) == "paused"
    tray.refresh_menu()
    assert tray.action("pause").text() == "Resume"
    act("pause")
    assert str(ctx.engine.state) == "focus"
    act("extend")
    assert ctx.engine.planned_seconds == 30 * 60
    assert ctx.pill.pill.time_text() == "25m"

    # Mini timer toggle and quiet time both hide the pill.
    act("pill")
    assert not ctx.pill.is_showing()
    act("pill")
    assert ctx.pill.is_showing()
    act("quiet")
    assert ctx.attention.state() == "quiet"
    ctx.attention.changed.emit("quiet")  # the monitor emits this on its next poll
    j.process()
    assert ctx.pill.is_showing()  # the explicit user toggle wins over quiet
    tray.refresh_menu()
    assert tray.action("quiet").text().startswith("End quiet time")
    act("quiet")
    assert ctx.attention.quiet_until is None

    act("finish")
    assert str(ctx.engine.state) == "idle"
    assert str(ctx.repo.get_session(ctx.repo.recent_sessions(limit=1)[0].id).outcome) == "ended_early"

    act("snooze")
    assert ctx.nudges.snoozed_until is not None

    # Wrap up the day… (twice): the pending summary of the finished session comes first, then
    # exactly one wrap-up sheet.
    from madhura.ui.overlays.session_complete import SessionCompleteSheet

    act("wrap")
    act("wrap")
    j.wait_active()
    sheets = [window.sheet_host.current(), *window.sheet_host.queued()]
    assert isinstance(sheets[0], SessionCompleteSheet) and sheets[0].pending
    assert sum(isinstance(s, WrapUpSheet) for s in sheets) == 1, sheets
    j.click(j.sheet().done_button)
    j.wait_until(lambda: isinstance(j.sheet(), WrapUpSheet))
    j.click(j.sheet().cancel_button)
    j.wait_until(lambda: not window.has_sheet() or not isinstance(j.sheet(), WrapUpSheet))
    assert not window.has_sheet()

    act("quit")
    assert j.quits == 1 and ctx.flags.get("shut_down")
    j.assert_clean()


def test_idle_away_panel_removes_time_with_the_real_window(journey: Journey, monkeypatch: pytest.MonkeyPatch) -> None:
    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, checkin_enabled=False)
    ctx = j.boot()
    _lock_in(j, "Read: Deep Work, chapter 4", 45)
    j.wait_until(lambda: not j.window.isVisible())
    j.advance(5 * 60)
    # Windows/macOS report input idle time: 8 minutes away from the keyboard.
    idle = {"s": 0.0}
    monkeypatch.setattr(ctx.attention, "input_idle_seconds", lambda: idle["s"])
    for _ in range(8 * 6):
        idle["s"] += 10
        j.advance(10, 10)
    assert ctx.away.is_away()
    idle["s"] = 0.5  # back
    j.advance(1, 1)
    panel = ctx.away.panel
    assert panel is not None and "away" in j.panel_kinds()
    assert panel.title.text() == "Away for 8 min"
    before = ctx.engine.focused_seconds
    j.click(panel.remove_button)
    assert before - ctx.engine.focused_seconds >= 8 * 60 - 1
    assert str(ctx.engine.state) == "focus"
    j.assert_clean()


def test_block_ending_while_away_pauses_where_you_left_with_the_away_banner(
    journey: Journey, monkeypatch: pytest.MonkeyPatch
) -> None:
    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, checkin_enabled=False)
    ctx = j.boot()
    sid = _lock_in(j, "Review the onboarding copy", 25)
    j.wait_until(lambda: not j.window.isVisible())
    # Windows/macOS: 5 minutes at the keyboard, then the PC is locked for lunch.
    idle = {"s": 0.0}
    monkeypatch.setattr(ctx.attention, "input_idle_seconds", lambda: idle["s"])
    j.advance(5 * 60)
    for _ in range(45 * 6):
        idle["s"] += 10
        j.advance(10, 10)
    engine = ctx.engine
    assert str(engine.state) == "paused" and engine.session_id == sid  # not "Session complete"
    assert 5 * 60 - 1 <= engine.focused_seconds <= 5 * 60 + 1
    assert ctx.repo.get_session(sid).focused_seconds == pytest.approx(5 * 60, abs=1)
    assert ctx.pill.pill.time_text() == "Paused · were you away?"
    assert "completion" not in j.panel_kinds()  # no "Nice — 25 min" for a block nobody finished

    idle["s"] = 0.5  # back: open Madhura
    j.qtbot.mouseDClick(ctx.pill.pill, Qt.MouseButton.LeftButton)
    j.wait_active()
    _settle_session(j)
    page = j.focus_page
    assert page.away_banner_visible()
    assert page.away_banner.text() == "You were away 20m"
    labels = [b.text() for b in page.away_banner.action_buttons()]
    assert labels == ["Resume", "Finish (save 5 min)", "Abandon"]
    assert ctx.away.panel is None  # nothing was credited, so nothing to remove
    j.click(page.away_banner.action_buttons()[1])
    row = ctx.repo.get_session(sid)
    assert str(row.outcome) == "ended_early" and row.focused_seconds == pytest.approx(5 * 60, abs=1)
    j.assert_clean()


def test_inline_focusing_back_then_switching_apps_records_the_drift(journey: Journey) -> None:
    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay")
    ctx = j.boot()
    window = j.window
    sid = _lock_in(j, "Clean the survey dataset", 45)
    _settle_session(j)
    strip = j.active.checkin_strip
    ck = ctx.checkins
    j.advance_until(lambda: ck.prompt_mode is not None, 8 * 60)
    assert ck.prompt_mode == "inline" and strip.phase() == "prompt"
    j.click(strip.back_button)
    assert strip.phase() == "reason"
    window.hide_main()  # straight back to the work app, within the 6 s reason window
    j.advance(2, 1)
    assert _records(ctx, sid) == [("drifted", None)]
    assert ck.prompt_mode is None and ck.popup is None and "checkin" not in j.panel_kinds()
    assert strip.phase() == "status"
    # The schedule goes on: the next check-in is a popup, one base interval after the drift.
    j.advance_until(lambda: ck.prompt_mode is not None, 8 * 60)
    assert ck.prompt_mode == "popup" and ck.popup.phase() == "question"
    j.click(ck.popup.focused_button)
    assert _records(ctx, sid)[-1] == ("focused", None)
    j.assert_clean()


def test_wrap_up_prompt_after_work_hours_opens_the_sheet(journey: Journey) -> None:
    from madhura.services.nudge_service import WRAP_UP_KEY
    from madhura.ui.overlays.wrap_up import WrapUpSheet

    j = journey
    j.clock.set(datetime(2026, 10, 14, 17, 50))
    j.write_settings(onboarded=True, reduce_motion=True)
    ctx = j.boot()
    j.idle.up_next.add_task("Fix the tray icon at 125% scaling", 45)
    window = j.window
    window.hide_main()
    j.process()
    # Inside work hours the gentle "What's next?" nudge would fire; snooze it past 18:00.
    ctx.nudges.snooze(15)
    for _ in range(25):  # 17:50 → 18:15
        j.advance(60, 30)
        ctx.nudges.check()
        j.process()
    panel = next((p for p in j.panels() if p.kind == "nudge"), None)
    assert panel is not None and panel.variant == "wrap_up"
    assert ctx.repo.kv_get(WRAP_UP_KEY) == "2026-10-14"
    j.click(panel.primary_button)
    j.wait_until(lambda: isinstance(j.sheet(), WrapUpSheet))
    assert window.isVisible() and window.current_page() == "focus"
    sheet = j.sheet()
    assert [r.task_id for r in sheet.task_rows]
    j.click(sheet.done_button)
    j.wait_until(lambda: not window.has_sheet())
    assert ctx.nudges.is_done_for_today
    j.assert_clean()


def test_killed_mid_focus_then_relaunched_hours_later_shows_away_banner(journey: Journey) -> None:
    j = journey
    j.write_settings(onboarded=True, reduce_motion=True, after_start="stay", checkin_enabled=False)
    ctx = j.boot()
    sid = _lock_in(j, "Rust ownership exercises", 60)
    j.advance(20 * 60, 30)  # heartbeats checkpoint every 30 s
    assert str(ctx.engine.state) == "focus"

    # The process dies (no quit_app, no pause); 3 hours later Madhura starts again.
    ctx2 = j.restart(after_seconds=3 * 3600)
    engine = ctx2.engine
    assert str(engine.state) == "paused" and engine.session_id == sid
    assert 20 * 60 - 31 <= engine.focused_seconds <= 20 * 60 + 1  # the dead time is not credited
    _settle_session(j)
    page = j.focus_page
    assert page.away_banner_visible()
    assert page.away_banner.text().startswith("You were away 3h")
    # Resume from the banner keeps going on the same session.
    j.click(page.away_banner.action_buttons()[0])
    assert str(engine.state) == "focus" and not page.away_banner_visible()
    j.assert_clean()
