"""UI kit: FloatingPanel/PopupStack, SheetHost, ToastHost, Popover and dialogs."""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QRect, QSize, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

from madhura.ui import dialogs, theme  # noqa: E402
from madhura.ui.widgets import (  # noqa: E402
    Button,
    ConfirmSheet,
    FloatingPanel,
    Popover,
    PopupStack,
    SheetContent,
    SheetHost,
    Stepper,
    TextField,
    ToastHost,
    compute_layout,
)
from madhura.ui.widgets import popup as popup_mod  # noqa: E402
from madhura.ui.widgets.sheet import sheet_width_of  # noqa: E402

_KEEP: list[QWidget] = []


@pytest.fixture(autouse=True)
def _theme(qapp: QApplication) -> Iterator[theme.ThemeManager]:
    tm = theme.ThemeManager("dark", "indigo", reduce_motion=True)
    tm.apply(qapp)
    PopupStack.reset()
    yield tm
    PopupStack.reset()
    for w in _KEEP:
        w.close()
    _KEEP.clear()
    theme.set_current(None)
    qapp.setStyleSheet("")


def _window(qtbot, w: int = 800, h: int = 600) -> QWidget:  # type: ignore[no-untyped-def]
    win = QWidget()
    lay = QVBoxLayout(win)
    lay.addWidget(QLineEdit(win))
    win.resize(w, h)
    qtbot.addWidget(win)
    _KEEP.append(win)
    win.show()
    QApplication.processEvents()
    return win


def _panel(kind: str, text: str = "Still on it?") -> FloatingPanel:
    p = FloatingPanel(kind)
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(QLabel(text))
    lay.addWidget(Button("Focused", "primary"))
    p.set_content(body)
    _KEEP.append(p)
    return p


# --------------------------------------------------------------------------- layout math


def test_compute_layout_stacks_bottom_up() -> None:
    avail = QRect(0, 0, 1000, 800)
    rects = compute_layout(avail, [QSize(340, 100), QSize(340, 120)])
    assert rects[0] == QRect(1000 - 16 - 340, 800 - 16 - 100, 340, 100)
    assert rects[1].bottom() == rects[0].top() - 10 - 1
    assert rects[1].right() == rects[0].right()
    assert compute_layout(avail, []) == []


def test_compute_layout_avoids_obstacle() -> None:
    avail = QRect(0, 0, 1000, 800)
    pill = QRect(800, 740, 180, 36)  # bottom-right pill
    rects = compute_layout(avail, [QSize(340, 100)], pill)
    assert rects[0].bottom() < pill.top() and pill.top() - (rects[0].bottom() + 1) == 10
    left_pill = QRect(20, 740, 180, 36)  # not in the column → ignored
    assert compute_layout(avail, [QSize(340, 100)], left_pill)[0].bottom() == 800 - 16 - 1
    top_pill = QRect(700, 30, 280, 700)  # tall obstacle: move left instead
    r = compute_layout(avail, [QSize(340, 100)], top_pill)[0]
    assert r.right() < top_pill.left()


# --------------------------------------------------------------------------- panels + stack


def test_floating_panel_flags_and_content(qtbot) -> None:  # type: ignore[no-untyped-def]
    p = _panel("checkin")
    flags = p.windowFlags()
    for f in (Qt.WindowType.Tool, Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint, Qt.WindowType.WindowDoesNotAcceptFocus):
        assert flags & f
    assert p.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert p.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert p.parent() is None and p.priority == 1
    btn = p.findChild(Button)
    assert btn is not None and btn.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert p.width() == 340 + 2 * 16
    assert p.card_size().width() == 340


def test_too_wide_content_is_laid_out_at_its_real_width(qtbot) -> None:  # type: ignore[no-untyped-def]
    """Qt widens a window to its minimum size; the stack must place the card at that width,
    or its right edge runs past the screen."""
    p = FloatingPanel("completion")
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    wide = QWidget()
    wide.setMinimumWidth(380)  # wider than the 340 − 2 × 16 content area
    lay.addWidget(wide)
    p.set_content(body)
    _KEEP.append(p)
    assert p.card_size().width() == p.minimumSizeHint().width() - 2 * 16 >= 380
    p.present()
    QApplication.processEvents()
    screen = p.screen().availableGeometry()
    assert p.visual_geometry().right() <= screen.right()
    assert p.visual_geometry().width() == p.card_size().width()
    p.dismiss(animate=False)
    QApplication.processEvents()


def test_stack_positions_and_priorities(qtbot) -> None:  # type: ignore[no-untyped-def]
    stack = PopupStack.instance()
    assert PopupStack.instance() is stack
    habit, completion = _panel("habit"), _panel("completion")
    habit.present()
    completion.present()
    assert stack.visible_panels() == [completion, habit]  # completion at the bottom
    screen = QGuiApplication.primaryScreen()
    avail = screen.availableGeometry()
    expected = compute_layout(avail, [completion.card_size(), habit.card_size()])
    assert completion.visual_geometry() == expected[0]
    assert habit.visual_geometry() == expected[1]
    assert completion.isVisible() and habit.isVisible()


def test_stack_max_three_and_queue(qtbot) -> None:  # type: ignore[no-untyped-def]
    stack = PopupStack.instance()
    panels = [_panel(k) for k in ("completion", "checkin", "away", "habit")]
    for p in panels:
        p.present()
    assert len(stack.visible_panels()) == 3 and stack.queued_panels() == [panels[3]]
    closed: list[int] = []
    panels[0].closed.connect(lambda: closed.append(1))
    panels[0].dismiss()
    qtbot.waitUntil(lambda: closed == [1], timeout=2000)
    assert not panels[0].isVisible()
    assert panels[3] in stack.visible_panels() and not stack.queued_panels()
    panels[1].dismiss()
    assert len(stack.visible_panels()) == 2


def test_nudge_exclusive(qtbot) -> None:  # type: ignore[no-untyped-def]
    stack = PopupStack.instance()
    checkin = _panel("checkin")
    nudge = _panel("nudge")
    checkin.present()
    nudge.present()
    assert stack.visible_panels() == [checkin] and stack.queued_panels() == [nudge]
    checkin.dismiss()
    assert stack.visible_panels() == [nudge]
    habit = _panel("habit")
    habit.present()  # a regular panel displaces the nudge back into the queue
    assert stack.visible_panels() == [habit] and nudge in stack.queued_panels()
    nudge_closed: list[int] = []
    nudge.closed.connect(lambda: nudge_closed.append(1))
    nudge.dismiss()  # dismissing a queued panel just removes it
    assert nudge_closed == [1] and not stack.contains(nudge)


def test_stack_avoids_obstacle(qtbot) -> None:  # type: ignore[no-untyped-def]
    stack = PopupStack.instance()
    avail = QGuiApplication.primaryScreen().availableGeometry()
    pill = FloatingPanel("pill", width=180)
    _KEEP.append(pill)
    pill.set_content(QLabel("23m"))
    card = QRect(avail.right() - 16 - 180, avail.bottom() - 16 - 36, 180, 36)
    pill.setGeometry(pill.window_rect_for_card(QRect(card.topLeft(), QSize(180, pill.card_size().height()))))
    pill.show()
    stack.set_obstacle(pill)
    panel = _panel("checkin")
    panel.present()
    assert panel.visual_geometry().bottom() < pill.visual_geometry().top()
    stack.set_obstacle(None)
    stack.relayout(animate=False)
    assert panel.visual_geometry().bottom() == avail.y() + avail.height() - 16 - 1


def test_content_changed_before_present_is_measured(qtbot) -> None:  # type: ignore[no-untyped-def]
    """A nested label shown after set_content() but before present() (e.g. the check-in intro)
    is part of the laid-out height: the card must not grow downward over the obstacle."""
    stack = PopupStack.instance()
    avail = QGuiApplication.primaryScreen().availableGeometry()
    pill = FloatingPanel("pill", width=120)
    _KEEP.append(pill)
    pill.set_content(QLabel("23m"))
    card = QRect(avail.right() - 16 - 120, avail.bottom() - 16 - 36, 120, 36)
    pill.setGeometry(pill.window_rect_for_card(QRect(card.topLeft(), QSize(120, pill.card_size().height()))))
    pill.show()
    stack.set_obstacle(pill)
    panel = FloatingPanel("checkin")
    _KEEP.append(panel)
    body = QWidget()
    outer = QVBoxLayout(body)
    outer.setContentsMargins(0, 0, 0, 0)
    inner = QWidget(body)
    inner_lay = QVBoxLayout(inner)
    inner_lay.setContentsMargins(0, 0, 0, 0)
    inner_lay.addWidget(QLabel("Still on it?"))
    intro = QLabel("Quick honesty check — only you see this.")
    inner_lay.addWidget(intro)
    intro.hide()
    outer.addWidget(inner)
    outer.addWidget(Button("Focused", "primary"))
    panel.set_content(body)  # measured without the intro
    intro.show()
    panel.present()
    for _ in range(3):
        QApplication.processEvents()
    assert panel.visual_geometry().bottom() < pill.visual_geometry().top()
    assert panel.visual_geometry().height() == panel.card_size().height()
    stack.set_obstacle(None)


def test_set_content_keeps_bottom_edge(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = _panel("checkin")
    panel.present()
    bottom = panel.visual_geometry().bottom()
    tall = QWidget()
    lay = QVBoxLayout(tall)
    for i in range(6):
        lay.addWidget(QLabel(f"Reason {i}"))
    panel.set_content(tall)
    QApplication.processEvents()
    assert panel.visual_geometry().bottom() == bottom
    assert panel.content() is tall


def test_hidden_panel_is_forgotten(qtbot) -> None:  # type: ignore[no-untyped-def]
    stack = PopupStack.instance()
    a, b = _panel("checkin"), _panel("habit")
    a.present()
    b.present()
    closed: list[int] = []
    a.closed.connect(lambda: closed.append(1))
    a.hide()  # owner hid it directly instead of dismiss()
    assert closed == [1] and stack.visible_panels() == [b]
    stack.relayout(animate=False)
    assert not a.isVisible()


def test_panel_width_is_animatable(qtbot) -> None:  # type: ignore[no-untyped-def]
    pill = FloatingPanel("pill", width=120)
    _KEEP.append(pill)
    pill.set_content(QLabel("23m"))
    pill.show()
    pill.setGeometry(QRect(100, 100, 300, pill.height()))
    assert pill.width() == 300 and pill.card_rect().width() == 300 - 32
    pill.set_card_width(200)
    assert pill.card_width() == 200 and pill.width() == 232


def test_shadow_cache() -> None:
    a = popup_mod.shadow_pixmap(14, 16, alpha=0.45, dpr=1.0)
    b = popup_mod.shadow_pixmap(14, 16, alpha=0.45, dpr=1.0)
    assert a is b and not a.isNull()
    c = popup_mod.shadow_pixmap(14, 16, alpha=0.45, dpr=2.0)
    assert c is not a and c.width() == a.width() * 2


def test_panel_animates_in_and_out(qtbot, _theme: theme.ThemeManager) -> None:  # type: ignore[no-untyped-def]
    _theme.set_reduce_motion(False)
    panel = _panel("away")
    panel.present()
    assert panel.isVisible()
    # A content/layout change during the entry animation must not freeze the fade.
    tall = QWidget()
    lay = QVBoxLayout(tall)
    for i in range(4):
        lay.addWidget(QLabel(f"line {i}"))
    panel.set_content(tall)
    qtbot.waitUntil(lambda: not panel.is_animating(), timeout=3000)
    assert panel.windowOpacity() == pytest.approx(1.0)
    stack = PopupStack.instance()
    expected = dict(stack.target_geometries())[panel]
    assert panel.geometry() == expected
    closed: list[int] = []
    panel.closed.connect(lambda: closed.append(1))
    panel.dismiss()
    qtbot.waitUntil(lambda: bool(closed), timeout=3000)
    assert not panel.isVisible() and panel.windowOpacity() == 1.0


# --------------------------------------------------------------------------- sheets


class _Sheet(SheetContent):
    def __init__(self, title: str, dismissible: bool = True) -> None:
        super().__init__()
        self.dismissible = dismissible
        self.set_title(title)
        self.field = TextField("Name")
        self.body.addWidget(self.field)
        self.ok = Button("Save", "primary")
        self.ok.clicked.connect(self.close_sheet)
        self.add_footer_button(self.ok, primary=True)


def test_sheet_queue_and_focus(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    before = win.findChild(QLineEdit)
    before.setFocus()
    host = SheetHost(win)
    a, b = _Sheet("A"), _Sheet("B")
    finished: list[str] = []
    a.finished.connect(lambda: finished.append("A"))
    b.finished.connect(lambda: finished.append("B"))
    active: list[bool] = []
    host.activeChanged.connect(active.append)
    host.show_sheet(a)
    host.show_sheet(b)
    assert host.has_sheet() and host.current() is a and host.queued() == [b]
    assert host.geometry() == win.rect()
    qtbot.waitUntil(lambda: a.field.line_edit.hasFocus(), timeout=2000)
    a.close_sheet()
    assert finished == ["A"] and host.current() is b
    qtbot.waitUntil(lambda: b.field.line_edit.hasFocus(), timeout=2000)
    host.close_sheet()
    assert finished == ["A", "B"] and not host.has_sheet() and host.isHidden()
    assert active == [True, False]
    assert before.hasFocus()  # focus restored


def test_tall_sheet_pins_its_footer(qtbot) -> None:  # type: ignore[no-untyped-def]
    """A sheet taller than the window scrolls its body under a pinned footer (the primary
    button stays reachable); when it fits again the footer goes back into the sheet."""
    win = _window(qtbot, 800, 420)
    host = SheetHost(win)
    sheet = _Sheet("Tall")
    for i in range(14):
        sheet.body.addWidget(QLabel(f"Row {i}"))
    host.show_sheet(sheet)
    QApplication.processEvents()
    assert sheet.footer_pinned()
    card = host.card_geometry()
    ok = sheet.ok.mapTo(host, sheet.ok.rect().bottomRight())
    assert card.contains(ok), "the pinned primary button is inside the visible card"
    assert not sheet.isAncestorOf(sheet.ok)  # it lives under the scroll area now
    qtbot.waitUntil(lambda: sheet.field.line_edit.hasFocus(), timeout=2000)
    QTest.keyClick(sheet.field.line_edit, Qt.Key.Key_Tab)
    assert sheet.ok.hasFocus()  # the focus trap includes the pinned footer
    win.resize(800, 900)
    QApplication.processEvents()
    assert not sheet.footer_pinned() and sheet.isAncestorOf(sheet.ok)
    win.resize(800, 420)
    QApplication.processEvents()
    assert sheet.footer_pinned()
    sheet.ok.click()  # Save closes; the footer is back in the content
    assert not host.has_sheet() and sheet.isAncestorOf(sheet.ok)


def test_sheet_scrollbar_stays_inside_the_rounded_card(qtbot) -> None:  # type: ignore[no-untyped-def]
    """The scrolling body's bar must not run along the card border or through the rounded
    top-right corner (radius 16)."""
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider

    from madhura.ui.widgets.sheet import SHEET_RADIUS

    win = _window(qtbot, 800, 420)
    host = SheetHost(win)
    sheet = _Sheet("Tall")
    for i in range(14):
        sheet.body.addWidget(QLabel(f"Row {i}"))
    host.show_sheet(sheet)
    QApplication.processEvents()
    bar = host._card.scroll.verticalScrollBar()  # noqa: SLF001
    assert bar.isVisible() and bar.sizeHint().width() == 10  # the pinned footer pads by this width
    opt = QStyleOptionSlider()
    bar.initStyleOption(opt)
    groove = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, opt, QStyle.SubControl.SC_ScrollBarGroove, bar)
    top_left = bar.mapTo(host, groove.topLeft())
    card = host.card_geometry()
    assert top_left.y() - card.top() >= SHEET_RADIUS
    assert card.right() - (top_left.x() + groove.width() - 1) >= 3


def test_sheet_escape_and_ctrl_enter(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    host = SheetHost(win)
    locked = _Sheet("Locked", dismissible=False)
    host.show_sheet(locked)
    qtbot.waitUntil(lambda: locked.field.line_edit.hasFocus(), timeout=2000)
    QTest.keyClick(locked.field.line_edit, Qt.Key.Key_Escape)
    assert host.current() is locked  # not dismissible
    QTest.keyClick(locked.field.line_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not host.has_sheet()  # primary (Save) clicked
    free = _Sheet("Free")
    host.show_sheet(free)
    qtbot.waitUntil(lambda: free.field.line_edit.hasFocus(), timeout=2000)
    QTest.keyClick(free.field.line_edit, Qt.Key.Key_Escape)
    assert not host.has_sheet()


def test_sheet_animated_close(qtbot, _theme: theme.ThemeManager) -> None:  # type: ignore[no-untyped-def]
    _theme.set_reduce_motion(False)
    win = _window(qtbot)
    host = SheetHost(win)
    s = _Sheet("Anim")
    done: list[int] = []
    s.finished.connect(lambda: done.append(1))
    host.show_sheet(s)
    qtbot.waitUntil(lambda: host._card.graphicsEffect() is None, timeout=2000)  # noqa: SLF001
    host.close_sheet()
    qtbot.waitUntil(lambda: bool(done), timeout=2000)
    assert host.isHidden() and host._card.graphicsEffect() is None  # noqa: SLF001


def test_confirm_sheet(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    host = SheetHost(win)
    s = ConfirmSheet("Reset all data?", "This deletes everything.", "Reset", danger=True, type_to_confirm="reset")
    acc: list[int] = []
    rej: list[int] = []
    s.accepted.connect(lambda: acc.append(1))
    s.rejected.connect(lambda: rej.append(1))
    host.show_sheet(s)
    assert s.ok_button.variant() == "danger_solid" and not s.ok_button.isEnabled()
    s.confirm_field.set_text("RESET")  # type: ignore[union-attr]
    assert s.ok_button.isEnabled()
    s.ok_button.click()
    assert acc == [1] and rej == [] and s.was_accepted() and not host.has_sheet()
    s2 = ConfirmSheet("Abandon session?", "", "Abandon")
    s2.rejected.connect(lambda: rej.append(2))
    host.show_sheet(s2)
    host.close_sheet()  # closed without a decision → rejected
    assert rej == [2]


def test_closed_sheets_are_deleted(qtbot) -> None:  # type: ignore[no-untyped-def]
    """A closed (or dequeued) sheet must not live on as a hidden parentless window: sheets whose
    closures capture them are cycles through C++ connections the GC never frees."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    win = _window(qtbot)
    host = SheetHost(win)
    shown = _Sheet("Shown")
    shown.ok.clicked.connect(lambda: shown.setWindowTitle("x"))  # self-capturing closure (cycle)
    queued = _Sheet("Queued")
    host.show_sheet(shown)
    host.show_sheet(queued)
    finished: list[str] = []
    queued.finished.connect(lambda: finished.append("queued"))
    host.close_sheet(queued)  # removed from the queue without ever opening
    assert finished == ["queued"]
    host.close_sheet()
    assert not host.has_sheet()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(shown) and not isValid(queued)
    assert not [w for w in QApplication.topLevelWidgets() if isinstance(w, SheetContent)]


def test_danger_confirm_sheet_opens_on_cancel(qtbot) -> None:  # type: ignore[no-untyped-def]
    """Space is the pause key (Focus) and the mark-done key (Habits): one stray press right after
    "Abandon…" / "Delete…" must cancel, never confirm. Ctrl+Enter still confirms."""
    win = _window(qtbot)
    host = SheetHost(win)
    decided: list[str] = []
    s = ConfirmSheet("Abandon this session?", "It won't count.", "Abandon", danger=True, cancel_text="Keep going")
    s.accepted.connect(lambda: decided.append("accepted"))
    s.rejected.connect(lambda: decided.append("rejected"))
    host.show_sheet(s)
    qtbot.waitUntil(lambda: s.cancel_button.hasFocus(), timeout=2000)
    QTest.keyClick(s.cancel_button, Qt.Key.Key_Space)
    assert decided == ["rejected"] and not host.has_sheet()

    s2 = ConfirmSheet("Delete ‘Read’?", "This removes its history.", "Delete habit", danger=True)
    s2.accepted.connect(lambda: decided.append("accepted 2"))
    host.show_sheet(s2)
    qtbot.waitUntil(lambda: s2.cancel_button.hasFocus(), timeout=2000)
    QTest.keyClick(s2.cancel_button, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert decided == ["rejected", "accepted 2"] and not host.has_sheet()

    s3 = ConfirmSheet("Restore this backup?", "", "Restart now")  # not destructive: starts on OK
    host.show_sheet(s3)
    qtbot.waitUntil(lambda: s3.ok_button.hasFocus(), timeout=2000)
    host.close_sheet()


def test_sheet_width_attribute() -> None:
    class Wide(SheetContent):
        width = 520  # type: ignore[assignment]  # spec-style int attribute

    class Narrow(SheetContent):
        sheet_width = 380

    assert sheet_width_of(Wide()) == 520
    assert sheet_width_of(Narrow()) == 380
    assert sheet_width_of(SheetContent()) == 440


# --------------------------------------------------------------------------- toasts


def test_toast_single_instance_and_action(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    host = ToastHost(win)
    expired: list[int] = []
    actioned: list[int] = []
    host.expired.connect(expired.append)
    host.actioned.connect(actioned.append)
    undone: list[int] = []
    t1 = host.show("Task deleted", "info", "Undo", lambda: undone.append(1))
    w = host.widget()
    assert host.is_showing() and w.isVisible() and host.action_text() == "Undo"
    assert w.geometry().center().x() == pytest.approx(win.width() / 2, abs=1)
    t2 = host.show("Session discarded")
    assert t2 != t1 and expired == [t1] and host.current_token() == t2
    assert host.action_text() == ""
    host.dismiss()
    assert expired == [t1, t2] and not host.is_showing()
    t3 = host.show("Capture cleared", "success", "Undo", lambda: undone.append(3))
    host.trigger_action()
    assert undone == [3] and actioned == [t3] and t3 not in expired


def test_toast_undo_runs_on_ctrl_z_but_text_fields_keep_their_undo(qtbot) -> None:  # type: ignore[no-untyped-def]
    """The toast never takes focus, so its Undo needs a key: Ctrl+Z (⌘Z) while an Undo toast
    shows — except in a text field, whose own undo wins. The action is a button to screen readers."""
    from PySide6.QtGui import QAccessible

    win = _window(qtbot)
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=2000)
    host = ToastHost(win)
    shortcut = host.undo_shortcut()
    ran: list[str] = []
    host.show("Exported", "success", "Show", lambda: ran.append("show"))
    assert not shortcut.isEnabled()  # only an "Undo" action gets the key
    host.show("Task deleted", "info", "Undo", lambda: ran.append("undo"))
    assert shortcut.isEnabled() and shortcut.key() == QKeySequence(QKeySequence.StandardKey.Undo)
    action = host.widget().action
    assert action.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert action.accessibleName() == "Undo" and action.accessibleDescription() == f"Shortcut: {host.undo_key_text()}"
    iface = QAccessible.queryAccessibleInterface(action)
    assert iface is not None and iface.role() == QAccessible.Role.Button

    edit = win.findChild(QLineEdit)
    edit.setFocus()
    QTest.keyClicks(edit, "abc")
    QTest.keyClick(edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert edit.text() != "abc"  # the field undid its typing …
    assert ran == [] and host.is_showing()  # … and the toast kept its Undo

    edit.clearFocus()
    QTest.keyClick(win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert ran == ["undo"] and not host.is_showing() and not shortcut.isEnabled()
    QTest.mouseClick(action, Qt.MouseButton.LeftButton)  # hidden now: nothing more runs
    assert ran == ["undo"]


def test_toast_action_click_runs_once(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    host = ToastHost(win)
    ran: list[int] = []
    host.show("Task deleted", "info", "Undo", lambda: ran.append(1))
    QApplication.processEvents()
    QTest.mouseClick(host.widget().action, Qt.MouseButton.LeftButton)
    assert ran == [1] and not host.is_showing()


def test_toast_timeout_and_hover_pause(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    host = ToastHost(win)
    t = host.show("Saved", duration_ms=1000)
    host._on_hover(True)  # noqa: SLF001 - pointer enters: countdown paused
    assert not host._timer.isActive()  # noqa: SLF001
    host._on_hover(False)  # noqa: SLF001
    assert host._timer.isActive() and host._timer.remainingTime() > 1000  # noqa: SLF001 - ≥ 1.5 s again
    with qtbot.waitSignal(host.expired, timeout=4000) as blocker:
        host._timer.start(10)  # noqa: SLF001 - skip the real 1.5 s
    assert blocker.args == [t]
    host.show("Bye")
    host.flush()
    assert not host.is_showing()


def test_toast_shown_while_hidden_waits_for_the_window(qtbot) -> None:  # type: ignore[no-untyped-def]
    """E.g. the "finished while Madhura was closed" notice at an autostart into the tray."""
    win = QWidget()
    win.resize(800, 600)
    qtbot.addWidget(win)
    _KEEP.append(win)
    host = ToastHost(win)
    expired: list[int] = []
    host.expired.connect(expired.append)
    t = host.show("Your session finished while Madhura was closed.", duration_ms=6000)
    assert host.is_showing() and host.is_held() and not host._timer.isActive()  # noqa: SLF001
    win.show()
    qtbot.waitUntil(lambda: host._timer.isActive(), timeout=1000)  # noqa: SLF001
    assert not host.is_held() and host._timer.remainingTime() > 5000  # noqa: SLF001 - the full 6 s
    assert host.widget().isVisible()
    # Minimized counts as hidden; restoring starts the countdown.
    win.showMinimized()
    QApplication.processEvents()
    if win.isMinimized():  # the platform tracks the state
        t2 = host.show("Held again")
        assert expired == [t] and host.is_held()
        win.showNormal()
        qtbot.waitUntil(lambda: host._timer.isActive(), timeout=1000)  # noqa: SLF001
        host.flush()
        assert expired == [t, t2]
    # A toast already counting keeps counting when the window goes away (undo commits on time).
    win.showNormal()
    QApplication.processEvents()
    host.show("Deleted — Undo", "info", "Undo", lambda: None)
    win.hide()
    QApplication.processEvents()
    assert host._timer.isActive() and not host.is_held()  # noqa: SLF001
    host.flush()


@pytest.mark.parametrize(("first", "second"), [("dark", "light"), ("light", "dark")])
def test_toast_restyles_live_on_theme_change(qtbot, _theme: theme.ThemeManager, first: str, second: str) -> None:  # type: ignore[no-untyped-def]
    """A toast that stays up across a theme switch takes the new theme's label colour."""
    from madhura.ui.widgets.toast import _toast_colors

    _theme.set_theme(first)
    win = _window(qtbot)
    host = ToastHost(win)
    host.show("Daily goal reached — 2h focused", "success", duration_ms=60_000)
    label = host.widget().label
    assert label.palette().color(label.foregroundRole()) == _toast_colors()[2]
    _theme.set_theme(second)
    QApplication.processEvents()
    expected = _toast_colors()[2]
    assert label.palette().color(label.foregroundRole()) == expected
    assert QColor(expected).lightness() > 200  # light text on the dark pill in both themes
    host.flush()


# --------------------------------------------------------------------------- popover


def test_popover_open_close(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    anchor = Button("Custom", parent=win)
    anchor.move(40, 80)
    anchor.show()
    stepper = Stepper(5, 240, 5, 35, suffix=" min")
    pop = Popover(anchor, stepper)
    closed: list[int] = []
    pop.closed.connect(lambda: closed.append(1))
    pop.open()
    assert pop.is_open() and pop.isVisible() and stepper.hasFocus()
    assert pop.geometry().top() > anchor.geometry().top()
    QTest.keyClick(stepper, Qt.Key.Key_Escape)
    assert closed == [1] and not pop.isVisible()
    pop.open()
    other = win.findChild(QLineEdit)
    QTest.mouseClick(other, Qt.MouseButton.LeftButton)
    assert closed == [1, 1]


def test_popover_closes_when_its_anchor_goes_away(qtbot) -> None:  # type: ignore[no-untyped-def]
    """A page switch (Ctrl+3), the idle/active swap or hiding to the tray hides the anchor's
    page: the popover must not float on over whatever is shown instead."""
    from PySide6.QtWidgets import QStackedWidget

    win = _window(qtbot)
    stack = QStackedWidget(win)
    win.layout().addWidget(stack)
    page, other_page = QWidget(), QWidget()
    stack.addWidget(page)
    stack.addWidget(other_page)
    anchor = Button("Custom", parent=page)
    anchor.move(40, 40)
    QApplication.processEvents()
    stepper = Stepper(5, 240, 5, 35, suffix=" min")
    pop = Popover(anchor, stepper)
    closed: list[int] = []
    pop.closed.connect(lambda: closed.append(1))
    anchor.setFocus(Qt.FocusReason.MouseFocusReason)  # clicked open
    pop.open()
    assert stepper.hasFocus()
    reasons: list[Qt.FocusReason] = []
    other_page.focusInEvent = lambda e: reasons.append(e.reason())  # type: ignore[method-assign]
    stack.setCurrentWidget(other_page)  # the page switch hides the anchor's page
    assert closed == [1] and not pop.isVisible()
    fw = QApplication.focusWidget()
    assert fw is stack, fw  # focus left the popover for what remains of the anchor's chain…
    keyboard = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
    assert not any(r in keyboard for r in reasons)  # …not for the next Tab stop (focus ring)
    stack.setCurrentWidget(page)
    pop.open()
    win.hide()  # hidden to the tray
    assert closed == [1, 1] and not pop.is_open()


def test_popover_follows_its_anchor_on_resize(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QHBoxLayout

    win = _window(qtbot, 800, 600)
    row = QHBoxLayout()
    row.addStretch(1)
    anchor = Button("Break", parent=win)
    row.addWidget(anchor)
    win.layout().addLayout(row)
    QApplication.processEvents()
    pop = Popover(anchor, QLabel("Start a 5-min break"), align="right")
    pop.open()
    gap = anchor.mapTo(win, anchor.rect().topRight()).x() - pop.geometry().right()
    win.resize(600, 600)  # the anchor moves 200 px left with the layout
    qtbot.waitUntil(lambda: anchor.mapTo(win, anchor.rect().topRight()).x() - pop.geometry().right() == gap, timeout=2000)
    assert pop.is_open()
    pop.dismiss()


# --------------------------------------------------------------------------- dialogs


def _click_when_modal(attr: str) -> None:
    def poke() -> None:
        dlg = QApplication.activeModalWidget()
        if isinstance(dlg, dialogs.StyledDialog):
            getattr(dlg.content, attr)()
        else:
            QTimer.singleShot(20, poke)

    QTimer.singleShot(20, poke)


def test_dialog_confirm(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)
    _click_when_modal("accept")
    assert dialogs.confirm(win, "Quit and pause your session?", "You can resume later.", "Quit") is True
    _click_when_modal("reject")
    assert dialogs.confirm(win, "Delete?", "Gone for good.", "Delete", danger=True) is False


def test_dialog_ask_text_and_messages(qtbot) -> None:  # type: ignore[no-untyped-def]
    win = _window(qtbot)

    def type_and_accept() -> None:
        dlg = QApplication.activeModalWidget()
        if not isinstance(dlg, dialogs.StyledDialog):
            QTimer.singleShot(20, type_and_accept)
            return
        dlg.content.field.set_text("Deep work")  # type: ignore[attr-defined]
        dlg.content._accept()  # type: ignore[attr-defined]  # noqa: SLF001

    QTimer.singleShot(20, type_and_accept)
    assert dialogs.ask_text(win, "Rename", "Task name", "old") == "Deep work"
    _click_when_modal("close_sheet")
    assert dialogs.ask_text(win, "Rename") is None
    _click_when_modal("close_sheet")
    dialogs.show_error(win, "Couldn't export", "Disk full.")
    _click_when_modal("close_sheet")
    dialogs.show_info(win, "Exported", "Saved to Downloads.")


def test_file_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("/tmp/x.csv", "CSV (*.csv)")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    assert dialogs.get_save_path(None, "Export", "sessions.csv", "CSV (*.csv)") == "/tmp/x.csv"
    assert dialogs.get_open_path(None, "Import", "JSON (*.json)") is None


def test_dialogs_are_monkeypatchable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    assert dialogs.confirm(None, "t", "x") is True

