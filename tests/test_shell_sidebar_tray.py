"""Sidebar (nav + status footer), tray (icon, tooltip, menu, toggle rule) and ShortcutsSheet."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from madhura.context import AppContext, FakeWindowController
from madhura.ui.main_window import MainWindow
from madhura.ui.overlays.wrap_up import WrapUpSheet
from madhura.ui.shortcuts_sheet import SHORTCUT_GROUPS, ShortcutsSheet, display_keys
from madhura.ui.sidebar import COLLAPSED_WIDTH, EXPANDED_WIDTH, Sidebar
from madhura.ui.tray import ICON_SIZES, Tray, render_tray_pixmap, tray_icon_for, tray_tooltip
from test_shell_support import advance, shell, tray_shell  # noqa: F401


def _seed_session(ctx: AppContext, minutes: int, start: datetime) -> None:
    sid = ctx.repo.create_session("Earlier work", None, None, start, minutes * 60)
    ctx.repo.update_session(sid, focused_seconds=minutes * 60, outcome="completed", ended_at=start)


# --------------------------------------------------------------------------- sidebar


def test_sidebar_structure_and_accessibility(shell: AppContext) -> None:  # noqa: F811
    sb = shell.window.sidebar  # type: ignore[union-attr]
    assert isinstance(sb, Sidebar)
    assert list(sb.items) == ["focus", "habits", "insights", "settings"]
    assert [i.label() for i in sb.items.values()] == ["Focus", "Habits", "Insights", "Settings"]
    for item in sb.items.values():
        assert item.accessibleName() == item.label()
        assert item.toolTip().startswith(item.label() + " · ")
        assert item.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert sb.items["focus"].is_active() and not sb.items["habits"].is_active()
    assert sb.width() == EXPANDED_WIDTH


def test_sidebar_click_does_not_toggle_and_emits(shell: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    sb = shell.window.sidebar  # type: ignore[union-attr]
    shell.window.show_main()  # type: ignore[union-attr]
    got: list[str] = []
    sb.navigate.connect(got.append)
    qtbot.mouseClick(sb.items["focus"], Qt.MouseButton.LeftButton)
    assert sb.items["focus"].is_active()  # clicking the active item keeps it active
    qtbot.mouseClick(sb.items["habits"], Qt.MouseButton.LeftButton)
    assert got == ["focus", "habits"] and sb.current() == "habits"
    assert not sb.items["focus"].is_active()


def test_sidebar_keyboard_navigation(shell: AppContext) -> None:  # noqa: F811
    win = shell.window
    assert isinstance(win, MainWindow)
    win.show_main()
    for _ in range(5):
        QApplication.processEvents()
    sb = win.sidebar
    sb.items["focus"].setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.sendEvent(sb.items["focus"], QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert sb.items["habits"].hasFocus()
    QApplication.sendEvent(sb.items["habits"], QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert win.current_page() == "habits"
    QApplication.sendEvent(sb.items["habits"], QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    QApplication.sendEvent(sb.items["focus"], QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    assert sb.items["settings"].hasFocus()  # wraps around


def test_sidebar_collapse_api(shell: AppContext) -> None:  # noqa: F811
    sb = shell.window.sidebar  # type: ignore[union-attr]
    sb.set_collapsed(True, animate=False)
    assert sb.is_collapsed() and sb.width() == COLLAPSED_WIDTH
    assert sb.items["focus"].toolTip() == "Focus"
    sb.set_collapsed(False, animate=False)
    assert sb.width() == EXPANDED_WIDTH and "·" in sb.items["focus"].toolTip()


def test_footer_idle_shows_progress_toward_goal(shell: AppContext) -> None:  # noqa: F811
    status = shell.window.sidebar.status  # type: ignore[union-attr]
    assert status.mode() == "idle" and status.status_text() == "0m of 2h today"
    _seed_session(shell, 80, datetime(2026, 10, 14, 8, 0))
    _seed_session(shell, 30, datetime(2026, 10, 13, 22, 0))  # yesterday: not counted
    shell.events.data_changed.emit("sessions")
    assert status.status_text() == "1h 20m of 2h today"
    assert abs(status.fraction() - 80 / 120) < 1e-6
    shell.settings.update(daily_goal_minutes=60)
    assert status.status_text() == "1h 20m of 1h today"


def test_footer_running_paused_break(shell: AppContext) -> None:  # noqa: F811
    status = shell.window.sidebar.status  # type: ignore[union-attr]
    # Nobody answers check-ins here; with them on, two misses would pause the session (§1.5).
    shell.settings.update(checkin_enabled=False)
    shell.engine.start("Write the quarterly report", 45)
    assert status.mode() == "focus" and status.status_text() == "45 min · Write the quarterly report"
    advance(shell, 22 * 60 + 10)
    assert status.status_text() == "23 min · Write the quarterly report"
    shell.engine.pause()
    assert status.status_text() == "Paused · Write the quarterly report"
    shell.engine.resume()
    advance(shell, 120)
    shell.engine.finish()
    assert status.mode() == "idle" and status.status_text().endswith("of 2h today")
    shell.engine.start_break(5)
    assert status.mode() == "break" and status.status_text() == "Break · 5 min"
    shell.engine.skip_break()
    assert status.mode() == "idle"


def test_footer_click_goes_to_focus(shell: AppContext, qtbot) -> None:  # noqa: F811, ANN001
    win = shell.window
    assert isinstance(win, MainWindow)
    win.show_main()
    win.show_page("insights")
    qtbot.mouseClick(win.sidebar.status, Qt.MouseButton.LeftButton)
    assert win.current_page() == "focus"


def test_footer_refreshes_on_day_changed(shell: AppContext) -> None:  # noqa: F811
    status = shell.window.sidebar.status  # type: ignore[union-attr]
    _seed_session(shell, 60, datetime(2026, 10, 14, 9, 0))
    shell.events.data_changed.emit("all")
    assert status.status_text() == "1h of 2h today"
    shell.clock.set(datetime(2026, 10, 15, 9, 0))  # type: ignore[attr-defined]
    shell.day_watcher.check()  # type: ignore[union-attr]
    assert status.status_text() == "0m of 2h today"


# --------------------------------------------------------------------------- tray


def test_tray_pixmaps_and_icon(shell: AppContext) -> None:  # noqa: F811
    for state in ("idle", "focus", "paused", "break"):
        for size in (16, 32):
            pm = render_tray_pixmap(size, state, 0.5)
            assert pm.width() == size and not pm.toImage().isNull()
    icon = tray_icon_for("focus", 0.5)
    assert {s.width() for s in icon.availableSizes()} >= set(ICON_SIZES)
    # the 16 px idle ring has opaque pixels on its edge and a transparent gap between ring and dot
    img = render_tray_pixmap(16, "idle", 1.0).toImage()
    assert img.pixelColor(8, 1).alpha() > 120
    assert img.pixelColor(8, 4).alpha() < 60


def test_tray_tooltip_text() -> None:
    assert tray_tooltip("idle", "", 0) == "Madhura"
    assert tray_tooltip("break", "", 60) == "Madhura — on a break"
    assert tray_tooltip("focus", "Write", 22 * 60 + 1) == "Madhura — 23 min left · Write"
    long = "x" * 100
    tip = tray_tooltip("focus", long, 60)
    assert tip.endswith("…") and len(tip.split(" · ", 1)[1]) == 60


def test_tray_icon_redraws_per_24th_turn(shell: AppContext) -> None:  # noqa: F811
    tray = Tray(shell)
    try:
        assert tray.icon_key()[0] == "idle"  # type: ignore[index]
        shell.engine.start("Write", 48)
        first = tray.icon_key()
        assert first[0] == "focus" and first[1] == 24  # type: ignore[index]
        advance(shell, 60)  # 1/48 of the session: below one 1/24 step → same icon
        assert tray.icon_key() == first
        advance(shell, 60)
        assert tray.icon_key()[1] == 23  # type: ignore[index]
        tray.refresh(force_tooltip=True)  # ticks are throttled to one tooltip per 15 s
        assert tray.tray_icon.toolTip().startswith("Madhura — 46 min left")
        shell.engine.pause()
        assert tray.icon_key()[0] == "paused"  # type: ignore[index]
        assert tray.tray_icon.toolTip().startswith("Madhura — paused")
    finally:
        tray.hide()


def test_tray_tooltip_throttled(shell: AppContext) -> None:  # noqa: F811
    tray = Tray(shell)
    try:
        shell.engine.start("Write", 30)
        tip = tray.tray_icon.toolTip()
        advance(shell, 120, step=30)  # real time hardly moves → throttled
        assert tray.tray_icon.toolTip() == tip
        tray.refresh(force_tooltip=True)
        assert tray.tray_icon.toolTip() != tip
    finally:
        tray.hide()


def test_tray_menu_states(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    ctx.window.show_main()  # type: ignore[union-attr] # pill_mode "hidden": no pill while the window is up
    tray = Tray(ctx)
    try:
        tray.refresh_menu()
        assert tray.action("start").isEnabled()
        assert not tray.action("pause").isEnabled() and not tray.action("finish").isEnabled()
        assert not tray.action("pill").isEnabled()
        ctx.engine.start("Write", 30)
        tray.menu.aboutToShow.emit()
        assert not tray.action("start").isEnabled()
        assert tray.action("pause").isEnabled() and tray.action("pause").text() == "Pause"
        tray.action("pause").trigger()
        tray.menu.aboutToShow.emit()
        assert str(ctx.engine.state) == "paused" and tray.action("pause").text() == "Resume"
        tray.action("pause").trigger()
        planned = ctx.engine.planned_seconds
        tray.action("extend").trigger()
        assert ctx.engine.planned_seconds == planned + 300
        assert tray.action("pill").text() == "Mini timer: Show" and tray.action("pill").isEnabled()
        ctx.window.hide_main()  # type: ignore[union-attr] # → the pill shows (§1.6)
        tray.menu.aboutToShow.emit()
        assert ctx.pill is not None and ctx.pill.is_showing()
        assert tray.action("pill").text() == "Mini timer: Hide"
        tray.action("pill").trigger()  # user-hidden until the session ends
        tray.menu.aboutToShow.emit()
        assert not ctx.pill.is_showing() and tray.action("pill").text() == "Mini timer: Show"
        advance(ctx, 90)
        tray.action("finish").trigger()
        assert str(ctx.engine.state) == "idle"
    finally:
        tray.hide()


def test_tray_quiet_snooze_wrap_and_quit(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    tray = Tray(ctx)
    try:
        tray.action("quiet").trigger()
        assert ctx.attention.is_quiet()
        tray.menu.aboutToShow.emit()
        assert tray.action("quiet").text().startswith("End quiet time")
        tray.action("quiet").trigger()
        assert not ctx.attention.is_quiet()
        tray.action("snooze").trigger()
        assert ctx.nudges is not None and ctx.nudges.snoozed_until is not None
        tray.action("wrap").trigger()
        assert isinstance(ctx.window.sheet_host.current(), WrapUpSheet)  # type: ignore[union-attr]
        assert ctx.window.current_page() == "focus"  # type: ignore[union-attr]
        tray.action("quit").trigger()
        assert ctx.flags["quit_calls"] == 1
    finally:
        tray.hide()


def test_tray_start_focus_shows_window(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    win = ctx.window
    assert isinstance(win, MainWindow)
    win.show_page("settings")
    tray = Tray(ctx)
    try:
        tray.action("start").trigger()
        assert win.is_main_visible() and win.current_page() == "focus"
    finally:
        tray.hide()


def test_tray_toggle_rule(shell: AppContext) -> None:  # noqa: F811
    tray = Tray(shell)
    fake = FakeWindowController(visible=False, active=False)
    shell.window, real = fake, shell.window
    try:
        tray.toggle_window()
        assert fake.calls[-1][0] == "show_main" and fake.is_main_active()
        tray.toggle_window()  # visible + active → hide
        assert fake.calls[-1][0] == "hide_main"
        fake.visible, fake.active = True, False
        tray.toggle_window()  # visible but inactive (and no recent activity) → bring forward
        assert fake.calls[-1][0] == "show_main"
        fake.active = False
        fake.was_active_recently = lambda ms: True  # type: ignore[attr-defined]
        tray.toggle_window()  # clicking the tray just deactivated us → hide
        assert fake.calls[-1][0] == "hide_main"
        n = len(fake.calls)
        tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)  # noqa: SLF001 - ignored
        assert len(fake.calls) == n
    finally:
        shell.window = real
        tray.hide()


def test_main_window_was_active_recently(shell: AppContext) -> None:  # noqa: F811
    win = shell.window
    assert isinstance(win, MainWindow)
    assert not win.was_active_recently()
    QApplication.sendEvent(win, QEvent(QEvent.Type.WindowDeactivate))
    assert win.was_active_recently(400)
    QApplication.sendEvent(win, QEvent(QEvent.Type.WindowActivate))
    assert not win.was_active_recently(400) or win.is_main_active()


def test_notifier_uses_tray(tray_shell: AppContext) -> None:  # noqa: F811
    ctx = tray_shell
    ctx.tray = Tray(ctx)
    try:
        assert ctx.notifier.notify("Hello", "World") in (True, False)  # offscreen: no visible tray
        assert ctx.notifier.sent[-1] == ("Hello", "World")
    finally:
        ctx.tray.hide()


# --------------------------------------------------------------------------- shortcuts sheet


def test_shortcuts_sheet_lists_every_global_shortcut(qapp: QApplication) -> None:
    sheet = ShortcutsSheet()
    keys = {k for _title, items in SHORTCUT_GROUPS for _t, k in items}
    for needed in ("Ctrl+1", "Ctrl+N", "Ctrl+Enter", "Space", "Ctrl+Shift+F", "Ctrl+H", "C", "Ctrl+,", "?", "Ctrl+Q", "Esc"):
        assert needed in keys
    assert len(sheet.rows) == sum(len(items) for _t, items in SHORTCUT_GROUPS)
    assert sheet.title_text() == "Keyboard shortcuts"
    assert sheet.primary_button() is not None
    assert display_keys("Ctrl+N") in ("Ctrl+N", "⌘+N")
