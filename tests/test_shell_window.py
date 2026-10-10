"""MainWindow: pages, navigation, sheets, toasts, shortcuts, visibility, close/quit, geometry."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication

from madhura.context import AppContext
from madhura.core.clock import FakeClock
from madhura.ui import dialogs
from madhura.ui.main_window import GEOMETRY_KEY, MINIMUM_SIZE, MainWindow, validate_geometry
from madhura.ui.pages.focus_page import FocusPage
from madhura.ui.pages.habit_editor import HabitEditorSheet
from madhura.ui.pages.habits_page import HabitsPage
from madhura.ui.pages.insights_page import InsightsPage
from madhura.ui.pages.settings_page import SettingsPage
from madhura.ui.shortcuts_sheet import ShortcutsSheet
from madhura.ui.widgets.sheet import SheetContent
from test_shell_support import advance, dispose_shell, make_shell, shell, tray_shell  # noqa: F401


def _win(ctx: AppContext) -> MainWindow:
    assert isinstance(ctx.window, MainWindow)
    return ctx.window


def _shortcut(win: MainWindow, seq: str):  # type: ignore[no-untyped-def]
    wanted = QKeySequence(seq)
    for sc in win.shortcuts():
        if sc.key() == wanted:
            return sc
    raise KeyError(seq)


def _activate(win: MainWindow) -> None:
    win.show_main(activate=True)
    for _ in range(5):
        QApplication.processEvents()
    assert win.is_main_active()


# --------------------------------------------------------------------------- pages / navigation


def test_pages_and_default_size(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    assert isinstance(win.pages["focus"], FocusPage)
    assert isinstance(win.pages["habits"], HabitsPage)
    assert isinstance(win.pages["insights"], InsightsPage)
    assert isinstance(win.pages["settings"], SettingsPage)
    assert win.current_page() == "focus"
    assert win.minimumSize() == MINIMUM_SIZE
    assert win.windowTitle() == "Madhura"


def test_pages_are_built_on_first_use(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    pages = win.pages
    assert list(pages) == ["focus", "habits", "insights", "settings"] and "settings" in pages
    assert pages.is_built("focus")  # type: ignore[attr-defined]
    assert not any(pages.is_built(k) for k in ("habits", "insights", "settings"))  # type: ignore[attr-defined]
    assert win.stack.count() == 1
    win.show_page("settings")
    assert pages.is_built("settings") and win.stack.currentWidget() is pages["settings"]  # type: ignore[attr-defined]
    assert pages["settings"] is pages["settings"] and win.stack.count() == 2  # built once
    assert "nope" not in pages and pages.get("nope") is None


def test_navigation_paths_and_on_shown(shell: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    win = _win(shell)
    win.show_main()
    calls: list[str] = []
    win.pages["insights"].on_shown = lambda: calls.append("insights")  # type: ignore[attr-defined]
    win.show_page("habits")
    assert win.current_page() == "habits" and win.stack.currentWidget() is win.pages["habits"]
    assert win.sidebar.current() == "habits"
    shell.events.navigate.emit("insights")
    assert win.current_page() == "insights" and calls == ["insights"]
    qtbot.mouseClick(win.sidebar.items["settings"], Qt.MouseButton.LeftButton)
    assert win.current_page() == "settings"
    win.show_page("nope")  # ignored
    assert win.current_page() == "settings"


def test_ctrl_number_shortcuts(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    for i, key in enumerate(("focus", "habits", "insights", "settings"), start=1):
        _shortcut(win, f"Ctrl+{i}").activated.emit()
        assert win.current_page() == key
    win.show_page("focus")
    _shortcut(win, "Ctrl+,").activated.emit()
    assert win.current_page() == "settings"


def test_page_switch_moves_focus_out_of_hidden_page(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    _activate(win)
    field = win.pages["focus"].idle_view.plan_card.task_field.line_edit
    field.setFocus()
    assert field.hasFocus()
    win.show_page("habits")
    fw = QApplication.focusWidget()
    assert fw is not None and win.pages["habits"].isAncestorOf(fw) or fw is win.pages["habits"]


def test_sidebar_collapses_below_1000(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.resize(1120, 760)
    win.show_main()
    assert not win.sidebar.is_collapsed() and win.sidebar.width() == 216
    win.resize(900, 700)
    QApplication.processEvents()
    assert win.sidebar.is_collapsed() and win.sidebar.width() == 64
    win.resize(1100, 700)
    assert not win.sidebar.is_collapsed() and win.sidebar.width() == 216


# --------------------------------------------------------------------------- lock in / session shortcuts


def test_lock_in_via_stub_starts_engine(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    seen: list[str] = []
    shell.events.data_changed.connect(seen.append)
    card = win.pages["focus"].idle_view.plan_card
    card.task_field.set_text("Write the intro")
    _shortcut(win, "Ctrl+Return").activated.emit()
    assert str(shell.engine.state) == "focus"
    assert shell.engine.task == "Write the intro"
    assert "sessions" in seen


def test_lock_in_button_and_empty_task_error(shell: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    win = _win(shell)
    win.show_main()
    card = win.pages["focus"].idle_view.plan_card
    qtbot.mouseClick(card.lock_in, Qt.MouseButton.LeftButton)
    assert str(shell.engine.state) == "idle" and card.task_field.has_error()
    card.task_field.set_text("Outline")
    qtbot.mouseClick(card.lock_in, Qt.MouseButton.LeftButton)
    assert str(shell.engine.state) == "focus"


def test_ctrl_enter_ignored_off_focus_page(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.pages["focus"].idle_view.plan_card.task_field.set_text("Write")
    win.show_page("habits")
    _shortcut(win, "Ctrl+Enter").activated.emit()
    assert str(shell.engine.state) == "idle"


def test_finish_and_hide_shortcuts(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    win = _win(ctx)
    win.show_main()
    _shortcut(win, "Ctrl+H").activated.emit()  # idle → nothing to hide to
    assert win.isVisible()
    ctx.engine.start("Write", 25)
    _shortcut(win, "Ctrl+H").activated.emit()
    assert not win.isVisible()
    advance(ctx, 120)
    _shortcut(win, "Ctrl+Shift+F").activated.emit()
    assert str(ctx.engine.state) == "idle"


def test_space_toggles_pause_on_focus_page(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    _activate(win)
    shell.engine.start("Write", 25)
    page = win.pages["focus"]
    page.setFocus()
    QApplication.sendEvent(page, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "))
    assert str(shell.engine.state) == "paused"


def test_ctrl_n_is_page_aware(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    _activate(win)
    win.show_page("habits")
    _shortcut(win, "Ctrl+N").activated.emit()
    assert isinstance(win.sheet_host.current(), HabitEditorSheet)
    win.close_sheet()
    assert not win.has_sheet()
    win.show_page("insights")
    calls: list[int] = []
    win.pages["focus"].focus_task_input = lambda: calls.append(1)  # type: ignore[attr-defined]
    _shortcut(win, "Ctrl+N").activated.emit()
    assert win.current_page() == "focus"
    assert calls == [1]


# --------------------------------------------------------------------------- focus_task_input deferral


def test_focus_task_input_deferred_until_activation(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    calls: list[int] = []
    win.pages["focus"].focus_task_input = lambda: calls.append(1)  # type: ignore[attr-defined]
    win.focus_task_input()  # hidden → deferred
    assert calls == []
    win.show_main()
    win.is_main_active = lambda: True  # type: ignore[method-assign]
    QApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    assert calls == [1]
    QApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    assert calls == [1]  # only once


def test_focus_task_input_waits_for_sheet(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    win.is_main_active = lambda: True  # type: ignore[method-assign]
    calls: list[int] = []
    win.pages["focus"].focus_task_input = lambda: calls.append(1)  # type: ignore[attr-defined]
    win.show_sheet(SheetContent())
    win.focus_task_input()
    assert calls == []
    win.close_sheet()
    assert calls == [1]


def test_set_nudge_banner_forwards(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    win.set_nudge_banner(True)
    assert win.pages["focus"].idle_view.nudge_banner.isVisibleTo(win)
    win.set_nudge_banner(False)
    assert not win.pages["focus"].idle_view.nudge_banner.isVisibleTo(win)


# --------------------------------------------------------------------------- sheets / toasts / shortcuts


def test_sheet_queue_fifo(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    a, b = SheetContent(), SheetContent()
    order: list[str] = []
    a.finished.connect(lambda: order.append("a"))
    b.finished.connect(lambda: order.append("b"))
    win.show_sheet(a)
    win.show_sheet(b)
    assert win.has_sheet() and win.sheet_host.current() is a and win.sheet_host.queued() == [b]
    win.close_sheet()
    assert win.sheet_host.current() is b and order == ["a"]
    win.close_sheet()
    assert not win.has_sheet() and order == ["a", "b"]


def test_shortcuts_disabled_while_sheet_open(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    quit_sc = _shortcut(win, "Ctrl+Q")
    page_scs = [sc for sc in win.shortcuts() if sc is not quit_sc]
    assert all(sc.isEnabled() for sc in win.shortcuts())
    win.show_sheet(SheetContent())
    assert not any(sc.isEnabled() for sc in page_scs)
    assert quit_sc.isEnabled()
    win.close_sheet()
    assert all(sc.isEnabled() for sc in win.shortcuts())


def test_question_mark_and_ctrl_slash_open_shortcuts_once(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    QApplication.sendEvent(win, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier, "?"))
    assert isinstance(win.sheet_host.current(), ShortcutsSheet)
    win.show_shortcuts()
    _shortcut(win, "Ctrl+/").activated.emit()  # disabled while the sheet is open anyway
    assert win.sheet_host.queued() == []
    sheet = win.sheet_host.current()
    assert len(sheet.rows) >= 14
    win.close_sheet()
    _shortcut(win, "Ctrl+/").activated.emit()
    assert isinstance(win.sheet_host.current(), ShortcutsSheet)


def test_escape_blurs_focus(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    _activate(win)
    field = win.pages["focus"].idle_view.plan_card.task_field.line_edit
    field.setFocus()
    assert field.hasFocus()
    QApplication.sendEvent(win, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not field.hasFocus()


def test_toasts(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    clicked: list[int] = []
    token = win.show_toast("Habit archived", "info", "Undo", lambda: clicked.append(1))
    assert isinstance(token, int) and win.toasts.is_showing()
    assert win.toasts.text() == "Habit archived" and win.toasts.action_text() == "Undo"
    win.toasts.trigger_action()
    assert clicked == [1] and not win.toasts.is_showing()
    shell.events.toast.emit("Saved", "success")
    assert win.toasts.text() == "Saved"
    expired: list[int] = []
    win.toasts.expired.connect(expired.append)
    win.flush_toasts()
    assert expired and not win.toasts.is_showing()


# --------------------------------------------------------------------------- visibility / close / quit


def test_main_visibility_changed(tray_shell: AppContext) -> None:  # noqa: F811
    win = _win(tray_shell)
    states: list[bool] = []
    tray_shell.events.main_visibility_changed.connect(states.append)
    win.show_main()
    assert states == [True] and win.is_main_visible()
    win.hide_main()
    assert states == [True, False] and not win.isVisible()
    win.show_main()
    win.showMinimized()
    QApplication.processEvents()
    if win.isMinimized():
        assert states[-1] is False and not win.is_main_visible()


def test_hide_main_minimizes_without_tray(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    win.hide_main()
    QApplication.processEvents()
    assert win.isVisible()  # never hidden without a tray (it would be unreachable)


def test_close_hides_to_tray_and_hints_once(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    win = _win(ctx)
    win.show_main()
    win.close()
    assert not win.isVisible() and ctx.flags["quit_calls"] == 0
    assert ctx.notifier.sent[-1][0] == "Madhura is still running in the tray"
    assert ctx.repo.kv_get("tray_hint_shown") is True
    win.show_main()
    win.close()
    assert sum(1 for t, _b in ctx.notifier.sent if t.startswith("Madhura is still running")) == 1


def test_close_quits_when_close_to_tray_off(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    ctx.settings.update(close_to_tray=False)
    win = _win(ctx)
    win.show_main()
    win.close()
    assert ctx.flags["quit_calls"] == 1 and win.quitting


def test_close_without_tray_quits(shell: AppContext) -> None:  # noqa: F811
    win = _win(shell)
    win.show_main()
    win.close()
    assert shell.flags["quit_calls"] == 1 and shell.flags["quitting"] is True


def test_quit_without_tray_confirms_and_pauses(shell: AppContext, monkeypatch) -> None:  # noqa: F811, ANN001
    win = _win(shell)
    win.show_main()
    shell.engine.start("Write", 30)
    answers = iter([False, True])
    asked: list[str] = []

    def fake_confirm(_parent, title, *_a, **_k):  # type: ignore[no-untyped-def]
        asked.append(title)
        return next(answers)

    monkeypatch.setattr(dialogs, "confirm", fake_confirm)
    win.quit_app()
    assert shell.flags["quit_calls"] == 0 and str(shell.engine.state) == "focus"
    _shortcut(win, "Ctrl+Q").activated.emit()
    assert shell.flags["quit_calls"] == 1 and str(shell.engine.state) == "paused"
    assert asked == ["Quit and pause your session?"] * 2


def test_quit_with_tray_pauses_without_asking(tray_shell: AppContext, monkeypatch) -> None:  # noqa: F811, ANN001
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    tray_shell.engine.start("Write", 30)
    _win(tray_shell).quit_app()
    assert tray_shell.flags["quit_calls"] == 1 and str(tray_shell.engine.state) == "paused"


# --------------------------------------------------------------------------- geometry


def test_validate_geometry() -> None:
    screens = [QRect(0, 0, 1920, 1040)]
    assert validate_geometry(None, screens) is None
    assert validate_geometry({"x": 1}, screens) is None
    rect, maxed = validate_geometry({"x": 100, "y": 50, "w": 1200, "h": 800, "maximized": True}, screens)  # type: ignore[misc]
    assert rect == QRect(100, 50, 1200, 800) and maxed
    assert validate_geometry({"x": 5000, "y": 50, "w": 1200, "h": 800}, screens) is None  # off-screen
    assert validate_geometry({"x": 100, "y": -500, "w": 1200, "h": 800}, screens) is None  # title bar unreachable
    small, _ = validate_geometry({"x": 0, "y": 0, "w": 10, "h": 10}, screens)  # type: ignore[misc]
    assert small.size() == MINIMUM_SIZE
    big, _ = validate_geometry({"x": 0, "y": 0, "w": 9000, "h": 9000}, screens)  # type: ignore[misc]
    assert big.width() == 1920 and big.height() == 1040


def test_geometry_round_trip(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> None:
    ctx = make_shell(qapp, data_dir, fake_clock, services=False)
    win = _win(ctx)
    win.show_main()
    win.setGeometry(QRect(40, 30, 960, 640))
    QApplication.processEvents()
    win.hide_main()  # minimizes (no tray) → saved on quit
    win.save_geometry()
    stored = ctx.repo.kv_get(GEOMETRY_KEY)
    assert stored["w"] == 960 and stored["h"] == 640
    dispose_shell(ctx, qapp)

    ctx2 = make_shell(qapp, data_dir, fake_clock, services=False)
    try:
        widest = max(s.availableGeometry().width() for s in QApplication.screens())
        expected_w = max(MINIMUM_SIZE.width(), min(960, widest))  # clamped to the screen
        assert _win(ctx2).size().width() == expected_w and _win(ctx2).size().height() == 640
        assert _win(ctx2).pos().x() == 40 and _win(ctx2).pos().y() == 30
    finally:
        dispose_shell(ctx2, qapp)


def test_bad_geometry_falls_back_to_default(qapp: QApplication, data_dir: Path, fake_clock: FakeClock) -> None:
    ctx = make_shell(qapp, data_dir, fake_clock, services=False)
    ctx.repo.kv_set(GEOMETRY_KEY, {"x": 99999, "y": 99999, "w": 1000, "h": 700})
    dispose_shell(ctx, qapp)
    ctx2 = make_shell(qapp, data_dir, fake_clock, services=False)
    try:
        win = _win(ctx2)
        assert win.width() >= MINIMUM_SIZE.width() and win.height() >= MINIMUM_SIZE.height()
        screen = QApplication.primaryScreen().availableGeometry()
        assert screen.intersects(win.geometry())
    finally:
        dispose_shell(ctx2, qapp)


def test_broken_page_shows_fallback(qapp: QApplication, data_dir: Path, fake_clock: FakeClock, monkeypatch) -> None:  # noqa: ANN001
    import madhura.ui.pages.insights_page as insights_mod

    def boom(_ctx: object) -> None:
        raise RuntimeError("broken page")

    monkeypatch.setattr(insights_mod, "InsightsPage", boom)
    ctx = make_shell(qapp, data_dir, fake_clock)
    try:
        win = _win(ctx)
        assert not isinstance(win.pages["insights"], InsightsPage)
        win.show_page("insights")
        assert win.current_page() == "insights"
    finally:
        dispose_shell(ctx, qapp)


def test_page_switch_never_gives_keyboard_focus_to_a_page_control(shell: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    """Switching pages hands focus to the new page itself: no Tab-reason focus (and keyboard
    focus ring) on its first control — e.g. the empty Insights page hides its range control."""
    from PySide6.QtCore import QObject

    win = _win(shell)
    win.show_main(activate=True)
    _activate(win)
    focus_page = win.page("focus")
    focus_page.setFocus(Qt.FocusReason.OtherFocusReason)  # the Focus page parks focus on itself
    reasons: list[tuple[str, Qt.FocusReason]] = []

    class Spy(QObject):
        def eventFilter(self, obj, event):  # type: ignore[no-untyped-def]  # noqa: N802
            if event.type() == QEvent.Type.FocusIn:
                reasons.append((type(obj).__name__, event.reason()))
            return False

    spy = Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        for key in ("insights", "habits", "settings", "focus"):
            win.show_page(key)
            QApplication.processEvents()
            assert QApplication.focusWidget() is win.page(key), key
    finally:
        QApplication.instance().removeEventFilter(spy)
    keyboard = {Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason}
    assert not [r for r in reasons if r[1] in keyboard], reasons
