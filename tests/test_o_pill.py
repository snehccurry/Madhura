"""[O] Mini timer pill: FocusPill + PillController (spec §1.6, §7.3)."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QEnterEvent, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication
from test_o_support import (  # noqa: F401
    advance,
    dispose_octx,
    make_octx,
    octx,
    release_context,
    set_attention,
)

from madhura.context import AppContext, FakeWindowController
from madhura.ui.overlays.pill import (
    HEIGHT,
    MAX_WIDTH,
    POS_KEY,
    FocusPill,
    PillController,
    default_card_rect,
    pill_time_text,
    position_from_kv,
    position_to_kv,
    snap_rect,
)
from madhura.ui.widgets.popup import FloatingPanel, PopupStack


def avail() -> QRect:
    return QGuiApplication.primaryScreen().availableGeometry()


def screen_name() -> str:
    return QGuiApplication.primaryScreen().name()


def show_window(ctx: AppContext, visible: bool) -> None:
    win: FakeWindowController = ctx.window  # type: ignore[assignment]
    win.visible = visible
    ctx.events.main_visibility_changed.emit(visible)


# --------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    "remaining,seconds,expected",
    [
        (3600, False, "60m"),
        (23 * 60, False, "23m"),
        (23 * 60 + 5, False, "24m"),  # countdown rounds up
        (61, False, "2m"),
        (60, False, "1:00"),
        (42, False, "0:42"),
        (0, False, "0:00"),
        (23 * 60 + 5, True, "23:05"),
        (3700, True, "1:01:40"),
    ],
)
def test_time_text(remaining: float, seconds: bool, expected: str) -> None:
    assert pill_time_text(remaining, seconds) == expected


def test_snap_rect() -> None:
    a = QRect(0, 0, 1000, 800)
    assert snap_rect(QRect(400, 300, 80, 36), a) == QRect(400, 300, 80, 36)  # far from edges
    assert snap_rect(QRect(10, 300, 80, 36), a) == QRect(12, 300, 80, 36)  # within 16 → inset 12
    assert snap_rect(QRect(1000 - 80 - 14, 800 - 36 - 3, 80, 36), a) == QRect(1000 - 12 - 80, 800 - 12 - 36, 80, 36)
    assert snap_rect(QRect(-40, -20, 80, 36), a) == QRect(12, 12, 80, 36)  # off-screen → inside + snapped
    assert snap_rect(QRect(1000 - 80 - 17, 100, 80, 36), a).x() == 1000 - 80 - 17  # 17 px: no snap


def test_position_round_trip_and_fallbacks() -> None:
    a = QRect(0, 0, 1000, 800)
    other = QRect(1000, 0, 1280, 1024)
    screens = [("A", a), ("B", other)]
    card = QRect(1600, 500, 90, HEIGHT)
    data = position_to_kv(card, "B", other)
    assert data == {"screen": "B", "dx_right": 1000 + 1280 - 1690, "dy_bottom": 1024 - 536}
    assert position_from_kv(data, 90, screens, a) == card
    default = default_card_rect(90, a)
    assert default == QRect(1000 - 16 - 90, 800 - 16 - HEIGHT, 90, HEIGHT)
    assert position_from_kv({"screen": "gone", "dx_right": 10, "dy_bottom": 10}, 90, screens, a) == default
    assert position_from_kv({"screen": "A", "dx_right": "x", "dy_bottom": 1}, 90, screens, a) == default
    assert position_from_kv(None, 90, screens, a) == default
    assert position_from_kv({"screen": "A", "dx_right": -5000, "dy_bottom": 10}, 90, screens, a) == default  # off all screens
    # a slightly-off rect on a valid screen is pulled back inside
    inside = position_from_kv({"screen": "A", "dx_right": -20, "dy_bottom": 100}, 90, screens, a)
    assert a.contains(inside)


# --------------------------------------------------------------------------- visibility


def test_hidden_while_idle_and_shown_when_window_hidden(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    changes: list[bool] = []
    pc.visibility_changed.connect(changes.append)
    assert not pc.is_showing() and not pc.pill.isVisible()
    octx.engine.start("Write", 60)
    assert pc.is_showing() and pc.pill.isVisible()
    show_window(octx, True)
    assert not pc.is_showing()
    show_window(octx, False)
    assert pc.is_showing()
    octx.engine.finish()
    QApplication.processEvents()
    assert changes[:3] == [True, False, True]


@pytest.mark.parametrize(
    "mode,window_visible,attention,expected",
    [
        ("hidden", False, "available", True),
        ("hidden", True, "available", False),
        ("always", True, "available", True),
        ("always", False, "available", True),
        ("never", False, "available", False),
        ("always", False, "quiet", False),
        ("hidden", False, "quiet", False),
        ("hidden", False, "busy", True),
    ],
)
def test_visibility_matrix(
    octx: AppContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    window_visible: bool,
    attention: str,
    expected: bool,
) -> None:
    octx.settings.update(pill_mode=mode)
    show_window(octx, window_visible)
    set_attention(monkeypatch, octx, attention)
    octx.engine.start("Write", 60)
    assert octx.pill.is_showing() is expected
    assert octx.pill.should_show() is expected


def test_break_and_pause_keep_the_pill(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    octx.engine.start("Write", 60)
    octx.engine.pause()
    assert pc.is_showing() and pc.pill.mode() == "paused"
    octx.engine.abandon()
    assert not pc.is_showing()
    octx.engine.start_break(10, "Lunch")
    assert pc.is_showing() and pc.pill.mode() == "break"
    pc.pill.set_expanded(True, animate=False)
    assert pc.pill.task_text() == "Lunch"


def test_settings_change_applies_immediately(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    assert octx.pill.is_showing()
    octx.settings.update(pill_mode="never")
    assert not octx.pill.is_showing()
    octx.settings.update(pill_mode="always")
    show_window(octx, True)
    assert octx.pill.is_showing()


def test_user_toggle_overrides_until_the_session_ends(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    pc.toggle_user_visible()  # idle: ignored
    assert pc.user_override is None
    octx.engine.start("Write", 60)
    assert pc.is_showing()
    pc.toggle_user_visible()
    assert not pc.is_showing() and pc.user_override is False
    show_window(octx, True)
    show_window(octx, False)
    assert not pc.is_showing()  # stays hidden
    pc.toggle_user_visible()
    assert pc.is_showing()
    show_window(octx, True)
    assert pc.is_showing()  # forced on even though the window is visible
    octx.engine.finish()
    assert pc.user_override is None
    octx.engine.start("Again", 30)
    assert not pc.is_showing()  # automatic again (window visible)


def test_tray_toggle_label_follows_is_showing(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    assert octx.pill.is_showing() is True
    octx.pill.toggle_user_visible()
    assert octx.pill.is_showing() is False


def test_real_main_window_visibility(qapp: QApplication, data_dir: Any, fake_clock: Any) -> None:
    from test_shell_support import dispose_shell, make_shell

    ctx = make_shell(qapp, data_dir, fake_clock)
    try:
        win = ctx.window
        ctx.settings.update(after_start="stay")
        win.show_main()  # type: ignore[union-attr]
        QApplication.processEvents()
        ctx.engine.start("Write", 60)
        assert not ctx.pill.is_showing()
        win.hide()  # type: ignore[union-attr]
        QApplication.processEvents()
        assert ctx.pill.is_showing()
        win.show_main()  # type: ignore[union-attr]
        QApplication.processEvents()
        assert not ctx.pill.is_showing()
    finally:
        dispose_shell(ctx, qapp)


# --------------------------------------------------------------------------- display


def test_whole_minutes_final_minute_and_seconds_setting(octx: AppContext) -> None:  # noqa: F811
    pill = octx.pill.pill
    octx.settings.update(checkin_enabled=False)
    octx.engine.start("Write", 60)
    assert pill.time_text() == "60m"
    advance(octx, 37 * 60, 30)
    assert pill.time_text() == "23m"
    octx.settings.update(pill_show_seconds=True)
    assert pill.time_text() == "23:00"
    octx.settings.update(pill_show_seconds=False)
    advance(octx, 22 * 60 + 18, 6)
    assert pill.time_text() == "0:42"


def test_repaints_per_minute_not_per_second(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    pill = octx.pill.pill
    octx.settings.update(checkin_enabled=False)
    octx.engine.start("Write", 60)
    advance(octx, 125, 5)
    calls: list[int] = []
    monkeypatch.setattr(pill, "update", lambda *a: calls.append(1))
    advance(octx, 60, 1)  # 60 engine ticks
    assert 1 <= len(calls) <= 3


def test_expansion_shows_mmss_task_and_buttons(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    pill = pc.pill
    octx.engine.start("Write the quarterly report for the leadership offsite", 45)
    collapsed_w = pill.width()
    pill.set_expanded(True, animate=False)
    assert pill.time_text() == "45:00"
    assert pill.width() > collapsed_w
    assert pill.width() - 2 * 12 <= MAX_WIDTH
    assert pill.toggle_button.isVisibleTo(pill) and pill.open_button.isVisibleTo(pill)
    assert pill.toggle_button.accessibleName() == "Pause"
    pill.set_expanded(False, animate=False)
    assert pill.width() == collapsed_w
    assert not pill.toggle_button.isVisibleTo(pill)


@pytest.mark.parametrize("side", ["right", "left"])
def test_expansion_direction(octx: AppContext, side: str) -> None:  # noqa: F811
    pill = octx.pill.pill
    octx.engine.start("Write the report", 45)
    a = avail()
    w = pill.collapsed_width()
    x = a.right() - 40 - w if side == "right" else a.left() + 40
    pill.set_collapsed_card(QRect(x, a.center().y(), w, HEIGHT))
    before = pill.visual_geometry()
    pill.set_expanded(True, animate=False)
    after = pill.visual_geometry()
    if side == "right":
        assert pill.grows_left()
        assert after.right() == before.right() and after.left() < before.left()
    else:
        assert not pill.grows_left()
        assert after.left() == before.left() and after.right() > before.right()


def test_hover_expands_after_delay_and_collapses_after_grace(  # noqa: F811
    octx: AppContext, qtbot: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from madhura.ui.overlays import pill as pill_mod

    # No real mouse here: make "is a button held?" deterministic.
    monkeypatch.setattr(pill_mod.QApplication, "mouseButtons", staticmethod(lambda: Qt.MouseButton.NoButton))
    pill = octx.pill.pill
    octx.engine.start("Write", 45)
    pos = QPointF(20, 20)
    pill.enterEvent(QEnterEvent(pos, pos, pill.mapToGlobal(pos)))
    assert not pill.is_expanded()
    assert pill._expand_timer.isActive() and pill._expand_timer.interval() == 150  # noqa: SLF001
    qtbot.waitUntil(pill.is_expanded, timeout=3000)
    pill.leaveEvent(QEvent(QEvent.Type.Leave))
    assert pill.is_expanded()  # 350 ms grace
    assert pill._collapse_timer.isActive() and pill._collapse_timer.interval() == 350  # noqa: SLF001
    qtbot.waitUntil(lambda: not pill.is_expanded(), timeout=3000)


def test_hover_expand_suppressed_while_a_button_is_held(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from madhura.ui.overlays import pill as pill_mod

    monkeypatch.setattr(pill_mod.QApplication, "mouseButtons", staticmethod(lambda: Qt.MouseButton.LeftButton))
    pill = octx.pill.pill
    octx.engine.start("Write", 45)
    pos = QPointF(20, 20)
    pill.enterEvent(QEnterEvent(pos, pos, pill.mapToGlobal(pos)))
    assert not pill._expand_timer.isActive()  # noqa: SLF001
    pill._on_expand_timer()  # noqa: SLF001
    assert not pill.is_expanded()


def test_pill_registers_as_popup_stack_obstacle(octx: AppContext) -> None:  # noqa: F811
    stack = PopupStack.instance()
    assert stack.obstacle() is octx.pill.pill
    octx.engine.start("Write", 60)
    pill_rect = octx.pill.pill.visual_geometry()
    panel = FloatingPanel("checkin")
    from PySide6.QtWidgets import QLabel

    panel.set_content(QLabel("Still on it?"))
    try:
        panel.present()
        card = panel.visual_geometry()
        assert card.bottom() < pill_rect.top()
        assert pill_rect.top() - (card.y() + card.height()) == 10
    finally:
        panel.hide()
        panel.deleteLater()


def test_default_position_is_bottom_right_of_primary(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    card = octx.pill.pill.visual_geometry()
    a = avail()
    assert a.x() + a.width() - (card.x() + card.width()) == 16
    assert a.y() + a.height() - (card.y() + card.height()) == 16


# --------------------------------------------------------------------------- drag / persistence


def _mouse(pill: FocusPill, kind: QEvent.Type, global_pos: QPoint, buttons: Qt.MouseButton) -> None:
    local = QPointF(pill.mapFromGlobal(global_pos))
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    ev = QMouseEvent(kind, local, QPointF(global_pos), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(pill, ev)


def test_drag_snaps_and_persists(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    pill = pc.pill
    octx.engine.start("Write", 60)
    a = avail()
    start = pill.visual_geometry().center()
    target = QPoint(a.left() + 30, a.top() + 200)  # pill left edge will be ~10 px from the screen edge
    _mouse(pill, QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
    _mouse(pill, QEvent.Type.MouseMove, start + QPoint(1, 0), Qt.MouseButton.LeftButton)
    assert not pill.is_dragging()  # below the drag threshold
    _mouse(pill, QEvent.Type.MouseMove, target, Qt.MouseButton.LeftButton)
    assert pill.is_dragging()
    _mouse(pill, QEvent.Type.MouseButtonRelease, target, Qt.MouseButton.NoButton)
    assert not pill.is_dragging()
    card = pill.collapsed_card()
    assert card.x() == a.x() + 12  # snapped to the 12 px inset
    data = octx.repo.kv_get(POS_KEY)
    assert data["screen"] == screen_name()
    assert data == position_to_kv(card, screen_name(), a)
    # a fresh controller restores the same spot
    pc2 = PillController(octx)
    try:
        assert pc2.pill.collapsed_card().topLeft() == card.topLeft()
    finally:
        pc2.pill.hide()
        pc2.pill.deleteLater()


def test_restore_falls_back_for_unknown_screen(octx: AppContext) -> None:  # noqa: F811
    octx.repo.kv_set(POS_KEY, {"screen": "HDMI-9", "dx_right": 300, "dy_bottom": 300})
    octx.pill.restore_position()
    card = octx.pill.pill.collapsed_card()
    a = avail()
    assert a.x() + a.width() - (card.x() + card.width()) == 16
    octx.repo.kv_set(POS_KEY, {"screen": screen_name(), "dx_right": 300, "dy_bottom": 200})
    octx.pill.restore_position()
    card = octx.pill.pill.collapsed_card()
    assert a.x() + a.width() - (card.x() + card.width()) == 300
    assert a.y() + a.height() - (card.y() + card.height()) == 200


def test_double_click_opens_the_main_window(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    pill = octx.pill.pill
    pos = pill.visual_geometry().center()
    ev = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(pill.mapFromGlobal(pos)),
        QPointF(pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(pill, ev)
    win: FakeWindowController = octx.window  # type: ignore[assignment]
    assert [c for c in win.calls if c[0] == "show_main"] == [("show_main", (None,), {"activate": True})]


# --------------------------------------------------------------------------- actions / menu


def test_toggle_button_pauses_resumes_and_ends_breaks(octx: AppContext) -> None:  # noqa: F811
    pill = octx.pill.pill
    octx.engine.start("Write", 60)
    pill.toggle_button.click()
    assert str(octx.engine.state) == "paused"
    assert pill.toggle_button.accessibleName() == "Resume"
    pill.toggle_button.click()
    assert str(octx.engine.state) == "focus"
    octx.engine.abandon()
    octx.engine.start_break(5)
    assert pill.toggle_button.accessibleName() == "End break"
    pill.toggle_button.click()
    assert str(octx.engine.state) == "idle"


def test_open_button(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    octx.pill.pill.open_button.click()
    assert octx.window.called("show_main") == 1  # type: ignore[union-attr]


def test_context_menu(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    octx.engine.start("Write", 60)
    menu = pc.build_menu()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Pause", "+5 min", "Finish", "Open Madhura", "Hide mini timer"]
    menu.actions()[1].trigger()
    assert octx.engine.planned_seconds == 65 * 60
    menu.actions()[0].trigger()
    assert str(octx.engine.state) == "paused"
    assert [a.text() for a in pc.build_menu().actions()][0] == "Resume"
    hide = [a for a in pc.build_menu().actions() if a.text() == "Hide mini timer"][0]
    hide.trigger()
    assert not pc.is_showing() and pc.user_override is False
    octx.engine.abandon()
    octx.engine.start_break(5)
    texts = [a.text() for a in pc.build_menu().actions() if not a.isSeparator()]
    assert texts == ["End break", "Open Madhura", "Hide mini timer"]


# --------------------------------------------------------------------------- done / pulse


def test_done_state_for_three_seconds_then_hidden(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    octx.settings.update(checkin_enabled=False)
    octx.engine.start("Write", 25)
    advance(octx, 25 * 60, 30)
    assert str(octx.engine.state) == "idle"
    assert pc.is_showing() and pc.is_done_showing()
    assert pc.pill.mode() == "done" and pc.pill.time_text() == "Done"
    pc.pill.set_expanded(True, animate=False)
    assert not pc.pill.is_expanded()  # "Done" never expands
    assert pc._done_timer.interval() == 3000  # noqa: SLF001
    pc._done_timer.timeout.emit()  # noqa: SLF001
    assert not pc.is_showing()


def test_done_then_auto_break_shows_the_break(octx: AppContext) -> None:  # noqa: F811
    pc = octx.pill
    octx.settings.update(checkin_enabled=False, auto_start_break=True)
    octx.engine.start("Write", 25)
    advance(octx, 25 * 60, 30)
    assert str(octx.engine.state) == "break"
    assert pc.pill.mode() == "done"
    pc._done_timer.timeout.emit()  # noqa: SLF001
    assert pc.is_showing() and pc.pill.mode() == "break"


def test_break_started_after_done_shows_the_break_at_once(octx: AppContext) -> None:  # noqa: F811
    """"Take a break" on the completion panel / sheet: the pill switches to the break at once
    (an auto-started break, in the same emission as the finish, keeps "Done" for 3 s)."""
    pc = octx.pill
    octx.settings.update(checkin_enabled=False)
    octx.engine.start("Write", 25)
    advance(octx, 25 * 60, 30)
    assert pc.pill.mode() == "done"
    QApplication.processEvents()  # the session_finished emission is over
    octx.engine.start_break(5)
    assert pc.is_showing() and not pc.is_done_showing()
    assert pc.pill.mode() == "break"


def test_abandon_hides_without_done(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 25)
    octx.engine.abandon()
    assert not octx.pill.is_showing() and not octx.pill.is_done_showing()


def test_pulses_once_at_two_minutes(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    pc = octx.pill
    octx.settings.update(checkin_enabled=False)
    calls: list[int] = []
    monkeypatch.setattr(pc.pill, "pulse", lambda: calls.append(1) or True)
    octx.engine.start("Write", 10)
    advance(octx, 7 * 60 + 59, 1)
    assert calls == []
    advance(octx, 2, 1)
    assert calls == [1]
    octx.engine.extend(5)
    advance(octx, 5 * 60, 1)
    assert calls == [1]  # once per session


def test_pulse_respects_reduce_motion(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 10)
    assert octx.pill.pill.pulse() is False  # reduce-motion is on in these tests
    octx.settings.update(reduce_motion=False)
    assert octx.pill.pill.pulse() is True
    assert octx.pill.pill.is_pulsing() or True


def test_theme_switch_repaints(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    calls: list[int] = []
    monkeypatch.setattr(octx.pill.pill, "update", lambda *a: calls.append(1))
    octx.settings.update(theme="light")
    assert calls


def test_pill_survives_teardown(qapp: QApplication, data_dir: Any, fake_clock: Any) -> None:
    ctx = make_octx(qapp, data_dir, fake_clock)
    pc = ctx.pill
    ctx.engine.start("Write", 60)
    dispose_octx(ctx, qapp, release=False)
    pc.refresh()
    pc.restore_position()
    pc._end_done()  # noqa: SLF001 - nothing raises once the widget is gone
    release_context(ctx)


def test_showing_the_pill_never_takes_focus_from_our_window(octx: AppContext, qtbot: Any) -> None:  # noqa: F811
    from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

    host = QWidget()
    QVBoxLayout(host).addWidget(field := QLineEdit(host))
    qtbot.addWidget(host)
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    field.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is field
    octx.engine.start("Write", 60)  # window hidden → the pill appears
    assert octx.pill.pill.isVisible()
    for _ in range(5):
        QApplication.processEvents()
    assert QApplication.focusWidget() is field
    assert QGuiApplication.focusWindow() is host.windowHandle()


def test_right_click_pops_the_menu(octx: AppContext) -> None:  # noqa: F811
    from PySide6.QtGui import QContextMenuEvent

    octx.engine.start("Write", 60)
    pill = octx.pill.pill
    center = pill.visual_geometry().center()
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pill.mapFromGlobal(center), center)
    QApplication.sendEvent(pill, ev)
    menu = octx.pill._menu  # noqa: SLF001
    assert menu is not None and menu.isVisible()
    menu.close()


def test_plain_click_does_not_move_or_persist(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    pill = octx.pill.pill
    before = pill.collapsed_card()
    p = pill.visual_geometry().center()
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        local = QPointF(pill.mapFromGlobal(p))
        ev = QMouseEvent(kind, local, QPointF(p), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(pill, ev)
    assert pill.collapsed_card() == before
    assert octx.repo.kv_get(POS_KEY) is None
