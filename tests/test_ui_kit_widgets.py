"""UI kit: construction and interaction of the basic widgets."""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QRegion, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from shiboken6 import isValid  # noqa: E402

from madhura.ui import theme  # noqa: E402
from madhura.ui.widgets import (  # noqa: E402
    Badge,
    Button,
    Caption,
    Card,
    Checkbox,
    Chip,
    ChipGroup,
    DayChips,
    ElidedLabel,
    EmptyState,
    Heading,
    IconButton,
    InlineBanner,
    Kbd,
    LinkButton,
    ListRow,
    Menu,
    Micro,
    PageHeader,
    PageScroll,
    ProgressRing,
    SectionHeader,
    SegmentedControl,
    Select,
    SettingsGroup,
    SettingsRow,
    Slider,
    Stepper,
    TextArea,
    TextField,
    TimeField,
    Toggle,
    install_wheel_guard,
)
from madhura.ui.widgets import _common  # noqa: E402


_KEEP: list[QWidget] = []  # pytest-qt only keeps weak references


@pytest.fixture(autouse=True)
def _theme(qapp: QApplication) -> Iterator[theme.ThemeManager]:
    tm = theme.ThemeManager("dark", "indigo", reduce_motion=True)
    tm.apply(qapp)
    yield tm
    for w in _KEEP:
        w.close()
    _KEEP.clear()
    theme.set_current(None)
    qapp.setStyleSheet("")


def _host(qtbot, *widgets: QWidget, width: int = 480) -> QWidget:  # type: ignore[no-untyped-def]
    w = QWidget()
    lay = QVBoxLayout(w)
    for x in widgets:
        lay.addWidget(x)
    w.resize(width, 400)
    qtbot.addWidget(w)
    _KEEP.append(w)
    w.show()
    QApplication.processEvents()
    return w


# --------------------------------------------------------------------------- buttons


@pytest.mark.parametrize("variant", ["primary", "secondary", "ghost", "danger", "danger_solid"])
@pytest.mark.parametrize("size,height", [("sm", 28), ("md", 36), ("lg", 40)])
def test_button_variants_and_sizes(qtbot, variant: str, size: str, height: int) -> None:  # type: ignore[no-untyped-def]
    b = Button("Lock in", variant, icon="play", size=size)
    _host(qtbot, b)
    assert b.height() == height and b.variant() == variant
    assert b.property("variant") == variant
    assert b.sizeHint().width() > 60
    clicks: list[int] = []
    b.clicked.connect(lambda: clicks.append(1))
    qtbot.mouseClick(b, Qt.MouseButton.LeftButton)
    assert clicks == [1]
    b.grab()  # paints without error


def test_button_api(qtbot) -> None:  # type: ignore[no-untyped-def]
    b = Button("Go")
    _host(qtbot, b)
    b.set_variant("primary")
    assert b.variant() == "primary"
    b.set_icon_name("check")
    assert b.icon_name() == "check"
    with pytest.raises(KeyError):
        b.set_icon_name("nope")


def test_icon_button_accessibility(qtbot) -> None:  # type: ignore[no-untyped-def]
    ib = IconButton("eye_off", "Hide to mini timer · Ctrl+H")
    _host(qtbot, ib)
    assert ib.toolTip() == "Hide to mini timer · Ctrl+H"
    assert ib.accessibleName() == "Hide to mini timer"
    assert ib.width() == 32 and ib.height() == 32
    ib.set_icon_name("pause")
    assert ib.icon_name() == "pause"
    with pytest.raises(KeyError):
        IconButton("nope", "x")
    small = IconButton("x", "Close", 24, accessible_name="Close banner")
    assert small.accessibleName() == "Close banner" and small.width() == 24


def test_focus_ring_only_for_keyboard(qtbot) -> None:  # type: ignore[no-untyped-def]
    a, b = Button("A"), Button("B")
    _host(qtbot, a, b)
    a.setFocus(Qt.FocusReason.MouseFocusReason)
    ring = _common._ring  # noqa: SLF001
    if ring is not None and isValid(ring):
        assert not ring.isVisible() or ring._target is not a  # noqa: SLF001
    b.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    ring = _common._ring  # noqa: SLF001
    assert ring is not None and ring._target is b and ring.isVisible()  # noqa: SLF001
    g = ring.geometry()
    bg = b.geometry() if ring.parentWidget() is b.parentWidget() else None
    if bg is not None:
        assert g.adjusted(4, 4, -4, -4) == bg
    a.setFocus(Qt.FocusReason.MouseFocusReason)
    assert not ring.isVisible()


def test_link_button(qtbot) -> None:  # type: ignore[no-untyped-def]
    lb = LinkButton("Wrap up the day", tone="accent", icon="sunset")
    _host(qtbot, lb)
    got: list[int] = []
    lb.clicked.connect(lambda: got.append(1))
    qtbot.mouseClick(lb, Qt.MouseButton.LeftButton)
    assert got == [1]


# --------------------------------------------------------------------------- chips


def test_chip_group_exclusive_click_and_keys(qtbot) -> None:  # type: ignore[no-untyped-def]
    g = ChipGroup([(15, "15m"), (25, "25m"), (60, "1h"), ("custom", "Custom")], value=25)
    _host(qtbot, g)
    changes: list[object] = []
    clicks: list[object] = []
    g.selectionChanged.connect(changes.append)
    g.chipClicked.connect(clicks.append)
    qtbot.mouseClick(g, Qt.MouseButton.LeftButton, pos=g.chip_rect(60).center())
    assert g.value() == 60 and changes == [60] and clicks == [60]
    qtbot.mouseClick(g, Qt.MouseButton.LeftButton, pos=g.chip_rect(60).center())
    assert changes == [60] and clicks == [60, 60]  # re-click: clicked, not changed
    g.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(g, Qt.Key.Key_Right)
    assert g.value() == "custom" and changes[-1] == "custom"
    QTest.keyClick(g, Qt.Key.Key_Left)
    assert g.value() == 60
    g.set_value(15)
    assert g.value() == 15 and changes[-1] == 60  # programmatic: no emit
    g.set_label("custom", "35m")
    assert g.label_for("custom") == "35m"


def test_chip_group_multi_and_wrapping(qtbot) -> None:  # type: ignore[no-untyped-def]
    g = ChipGroup(["Phone", "Social media", "Messages", "Email", "Browsing", "Daydreaming", "Other"], exclusive=False)
    _host(qtbot, g, width=300)
    got: list[object] = []
    g.selectionChanged.connect(got.append)
    qtbot.mouseClick(g, Qt.MouseButton.LeftButton, pos=g.chip_rect("Email").center())
    qtbot.mouseClick(g, Qt.MouseButton.LeftButton, pos=g.chip_rect("Phone").center())
    assert got[-1] == ["Phone", "Email"]
    qtbot.mouseClick(g, Qt.MouseButton.LeftButton, pos=g.chip_rect("Phone").center())
    assert g.values() == ["Email"]
    assert g.hasHeightForWidth() and g.heightForWidth(200) > g.heightForWidth(2000)
    assert g.height() >= g.heightForWidth(g.width())


def test_chip_single(qtbot) -> None:  # type: ignore[no-untyped-def]
    c = Chip("Read 10 pages")
    _host(qtbot, c)
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton)
    assert c.isChecked()
    plain = Chip("Phone", checkable=False)
    assert not plain.isCheckable()


def test_segmented_control(qtbot) -> None:  # type: ignore[no-untyped-def]
    s = SegmentedControl(["7 days", "30 days", "12 weeks"], value="7 days")
    _host(qtbot, s)
    got: list[object] = []
    s.valueChanged.connect(got.append)
    rects = s._segment_rects()  # noqa: SLF001
    qtbot.mouseClick(s, Qt.MouseButton.LeftButton, pos=rects[2].center().toPoint())
    assert s.value() == "12 weeks" and got == ["12 weeks"]
    s.setFocus()
    QTest.keyClick(s, Qt.Key.Key_Left)
    assert s.value() == "30 days" and got[-1] == "30 days"
    s.set_value("7 days")
    assert got[-1] == "30 days"


def test_day_chips(qtbot) -> None:  # type: ignore[no-untyped-def]
    d = DayChips(0b0011111)
    _host(qtbot, d)
    got: list[int] = []
    d.maskChanged.connect(got.append)
    qtbot.mouseClick(d, Qt.MouseButton.LeftButton, pos=d._rect(5).center())  # noqa: SLF001 - Saturday
    assert d.mask() == 0b0111111 and got == [0b0111111]
    d.setFocus()
    QTest.keyClick(d, Qt.Key.Key_Home)
    QTest.keyClick(d, Qt.Key.Key_Space)
    assert not d.is_day_set(0) and got[-1] == 0b0111110
    d.set_mask(127)
    assert d.mask() == 127 and got[-1] == 0b0111110


# --------------------------------------------------------------------------- toggles


def test_toggle(qtbot) -> None:  # type: ignore[no-untyped-def]
    t = Toggle(False, accessible_name="Check-ins")
    _host(qtbot, t)
    got: list[bool] = []
    t.toggled.connect(got.append)
    qtbot.mouseClick(t, Qt.MouseButton.LeftButton)
    assert t.isChecked() and got == [True]
    t.setFocus()
    QTest.keyClick(t, Qt.Key.Key_Space)
    assert not t.isChecked()
    assert t.accessibleName() == "Check-ins"


def _contrast(a: QColor, b: QColor) -> float:
    def lum(c: QColor) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_disabled_toggle_stays_visible(qtbot, _theme: theme.ThemeManager, scheme: str) -> None:  # type: ignore[no-untyped-def]
    """A disabled switch keeps a visible outline/knob on the card surface in both themes, and
    on/off remain distinguishable."""
    _theme.set_theme(scheme)
    surface = QColor(theme.palette().surface)
    off, on = Toggle(False), Toggle(True)
    _host(qtbot, off, on)
    images = []
    for t in (off, on):
        t.setEnabled(False)
        img = QImage(t.size(), QImage.Format.Format_ARGB32)
        img.fill(surface)
        t.render(img, QPoint(), QRegion(), QWidget.RenderFlag(0))
        images.append(img)
        best = max(_contrast(QColor(img.pixel(x, y)), surface) for x in range(img.width()) for y in range(img.height()))
        assert best >= 1.6, (scheme, t.isChecked(), best)
    mid = QPoint(8, off.height() // 2)  # left of the "on" knob, on the "off" knob
    assert images[0].pixelColor(mid) != images[1].pixelColor(mid)


def test_checkbox(qtbot) -> None:  # type: ignore[no-untyped-def]
    c = Checkbox(True, box=28)
    s = Checkbox(text="Mark done", checked=True)
    _host(qtbot, c, s)
    assert c.is_round() and c.height() >= 32
    got: list[bool] = []
    c.toggled.connect(got.append)
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton)
    assert c.isChecked() and got == [True]
    c.set_dashed(True)
    c.grab()
    assert s.isChecked() and s.accessibleName() == "Mark done"


# --------------------------------------------------------------------------- ring


def test_progress_ring(qtbot) -> None:  # type: ignore[no-untyped-def]
    r = ProgressRing(280, 12)
    _host(qtbot, r)
    assert r.accessibleName() == "Time remaining" and r.width() == 280
    r.set_progress(1.5)
    assert r.progress() == 1.0
    r.set_progress(0.4, animate=True)  # reduce-motion → immediate
    assert r.displayed_progress() == pytest.approx(0.4)
    r.set_text("24:59")
    r.set_caption("of 60 min")
    r.set_color("#FF0000")
    r.set_color(None)
    r.set_track_color("#00FF00")
    r.set_breathing(True)
    assert not r.is_breathing()  # reduce-motion
    r.grab()


def test_progress_ring_animates(qtbot, _theme: theme.ThemeManager) -> None:  # type: ignore[no-untyped-def]
    _theme.set_reduce_motion(False)
    r = ProgressRing(40, 4)
    _host(qtbot, r)
    r.set_progress(1.0, animate=True, duration=80)
    assert r.progress() == 1.0 and r.displayed_progress() < 1.0
    qtbot.waitUntil(lambda: r.displayed_progress() == pytest.approx(1.0), timeout=2000)
    r.set_breathing(True)
    assert r.is_breathing()
    r.set_breathing(False)
    assert not r.is_breathing()


# --------------------------------------------------------------------------- inputs


def test_text_field(qtbot) -> None:  # type: ignore[no-untyped-def]
    f = TextField("What will you focus on?", icon="target", height=52)
    _host(qtbot, f)
    assert f.frame.height() == 52 and f.line_edit.font().pixelSize() == 18
    returned: list[int] = []
    texts: list[str] = []
    f.returnPressed.connect(lambda: returned.append(1))
    f.textChanged.connect(texts.append)
    f.set_error("Name the one thing you'll work on")
    assert f.has_error() and f.error_text().startswith("Name")
    f.line_edit.setFocus()
    QTest.keyClicks(f.line_edit, "Write")
    assert not f.has_error()  # typing clears the error
    QTest.keyClick(f.line_edit, Qt.Key.Key_Return)
    assert returned == [1] and texts[-1] == "Write" and f.text() == "Write"
    f.set_leading_color("#FF0000")
    f.set_trailing_widget(Badge("25m"))
    f.set_text("abc", select=True)
    assert f.line_edit.selectedText() == "abc"


def test_text_area(qtbot) -> None:  # type: ignore[no-untyped-def]
    t = TextArea("Optional note", lines=3)
    _host(qtbot, t)
    t.set_text("hello")
    assert t.text() == "hello" and t.height() > 60


def test_time_field(qtbot) -> None:  # type: ignore[no-untyped-def]
    t = TimeField("09:05")
    _host(qtbot, t)
    got: list[str] = []
    t.timeChanged.connect(got.append)
    assert t.time_str() == "09:05"
    t.time_edit.setFocus()
    QTest.keyClick(t.time_edit, Qt.Key.Key_Up)
    assert t.time_str() == "09:15" and got == ["09:15"]
    QTest.keyClick(t.time_edit, Qt.Key.Key_Down)
    QTest.keyClick(t.time_edit, Qt.Key.Key_Down)
    assert t.time_str() == "08:45"
    t.set_time_str("23:50")
    assert got[-1] == "08:45"  # programmatic: no emit
    QTest.keyClick(t.time_edit, Qt.Key.Key_Up)
    assert t.time_str() == "00:00"  # wraps at midnight
    t.set_time_str("bogus")
    assert t.time_str() == "00:00"


def test_stepper(qtbot) -> None:  # type: ignore[no-untyped-def]
    s = Stepper(5, 240, 5, 35, suffix=" min")
    _host(qtbot, s)
    got: list[float] = []
    s.valueChanged.connect(got.append)
    assert s.text() == "35 min"
    qtbot.mouseClick(s._plus, Qt.MouseButton.LeftButton)  # noqa: SLF001
    assert s.value() == 40 and got == [40.0]
    s.setFocus()
    QTest.keyClick(s, Qt.Key.Key_Down)
    assert s.int_value() == 35
    QTest.keyClick(s, Qt.Key.Key_End)
    assert s.value() == 240 and not s._plus.isEnabled()  # noqa: SLF001
    s.set_value(3)
    assert s.value() == 5 and got[-1] == 240
    hours = Stepper(0.5, 12, 0.5, 2, fmt=lambda v: f"{v:g} h")
    assert hours.text() == "2 h"


def test_slider_and_wheel_guard(qtbot, qapp: QApplication) -> None:  # type: ignore[no-untyped-def]
    install_wheel_guard(qapp)
    assert install_wheel_guard(qapp) is install_wheel_guard(qapp)
    s = Slider(0, 100, 40)
    other = Button("x")
    _host(qtbot, s, other)
    released: list[int] = []
    s.valueReleased.connect(released.append)
    other.setFocus()

    def wheel() -> None:
        pos = QPointF(s.width() / 2, s.height() / 2)
        ev = QWheelEvent(pos, QPointF(s.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(s, ev)

    wheel()
    assert s.value() == 40  # unfocused: ignored
    s.setFocus()
    wheel()
    assert s.value() != 40
    qtbot.mouseClick(s, Qt.MouseButton.LeftButton, pos=QPoint(s.width() - 4, s.height() // 2))
    assert released == [s.value()] and s.value() > 80  # groove click: exactly one release


def test_slider_reports_each_release_once(qtbot) -> None:  # type: ignore[no-untyped-def]
    s = Slider(0, 100, 40)
    _host(qtbot, s)
    released: list[int] = []
    s.valueReleased.connect(released.append)
    y = s.height() // 2
    qtbot.mouseClick(s, Qt.MouseButton.LeftButton, pos=QPoint(s.width() // 4, y))  # groove → jump
    assert len(released) == 1 and released[-1] == s.value() < 40
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider

    opt = QStyleOptionSlider()
    s.initStyleOption(opt)
    handle = s.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, s)
    qtbot.mousePress(s, Qt.MouseButton.LeftButton, pos=handle.center())  # grab the handle and drag
    qtbot.mouseMove(s, QPoint(s.width() - 6, y))
    assert len(released) == 1  # nothing while dragging
    qtbot.mouseRelease(s, Qt.MouseButton.LeftButton, pos=QPoint(s.width() - 6, y))
    assert len(released) == 2 and released[-1] == s.value() > 80
    qtbot.mouseClick(s, Qt.MouseButton.RightButton, pos=QPoint(s.width() // 2, y))
    s.setSliderDown(True)
    s.setSliderDown(False)  # programmatic release still reports once
    assert len(released) == 3


def test_select(qtbot) -> None:  # type: ignore[no-untyped-def]
    sel = Select([(5, "5 min"), (7, "7 min"), (10, "10 min")], 7)
    _host(qtbot, sel)
    got: list[object] = []
    sel.valueChanged.connect(got.append)
    assert sel.current_label() == "7 min"
    sel.set_value(10)
    assert sel.value() == 10 and got == []
    sel.setFocus()
    QTest.keyClick(sel, Qt.Key.Key_Up)
    assert sel.value() == 7 and got == [7]
    sel.open_menu()
    menu = sel.menu()
    assert menu is not None
    actions = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["5 min", "7 min", "10 min"]
    assert actions[1].isChecked() and not actions[0].isChecked()
    actions[0].trigger()
    assert sel.value() == 5 and got[-1] == 5
    menu.hide()


def test_menu(qtbot) -> None:  # type: ignore[no-untyped-def]
    m = Menu()
    called: list[str] = []
    a = m.add_action("Pause", lambda: called.append("pause"), icon="pause", shortcut_text="Space")
    m.add_separator()
    d = m.add_action("Delete", lambda: called.append("del"), icon="trash", danger=True, enabled=False)
    c = m.add_action("Show seconds", checkable=True, checked=True)
    sub = m.add_menu("More", icon="more")
    sub.add_action("Nested")
    a.trigger()
    assert called == ["pause"]
    assert not d.isEnabled() and c.isChecked()
    assert a.property("mh_shortcut") == "Space"
    assert m.minimumWidth() >= 180
    anchor = Button("anchor")
    _host(qtbot, anchor)
    m.popup_below(anchor)
    qtbot.waitUntil(m.isVisible, timeout=2000)
    m.grab()
    m.hide()


# --------------------------------------------------------------------------- labels & structure


def test_labels(qtbot) -> None:  # type: ignore[no-untyped-def]
    h, cap, mic = Heading("Habits"), Caption("Ends at 14:35"), Micro("this week")
    el = ElidedLabel("A very long task title that should certainly be elided somewhere", "label")
    badge = Badge("25m", "accent", clickable=True)
    kbd, plus = Kbd("Ctrl+Shift+F"), Kbd("Ctrl++")
    header = SectionHeader("Morning", 3, LinkButton("Edit"))
    _host(qtbot, h, cap, mic, el, badge, kbd, plus, header, width=200)
    assert h.property("role") == "h1" and cap.property("role") == "caption"
    assert mic.text() == "THIS WEEK"
    assert el.text().endswith("…") and el.full_text().startswith("A very") and el.toolTip()
    clicks: list[int] = []
    badge.clicked.connect(lambda: clicks.append(1))
    qtbot.mouseClick(badge, Qt.MouseButton.LeftButton)
    assert clicks == [1]
    badge.set_tone("bogus")
    assert badge.tone() == "neutral"
    assert kbd._keys == ["Ctrl", "Shift", "F"] and plus._keys == ["Ctrl", "+"]  # noqa: SLF001
    header.set_count(None)
    header.set_count(5)


def test_card(qtbot) -> None:  # type: ignore[no-untyped-def]
    c = Card("Up next", "3 tasks", LinkButton("Add"))
    c.body.addWidget(QLabel("content"))
    _host(qtbot, c)
    assert c.title_label() is not None and c.title_label().text() == "Up next"
    c.set_title(None)
    c.set_action(None)
    c.set_background("surface_alt", None)
    c.grab()


def test_list_row(qtbot) -> None:  # type: ignore[no-untyped-def]
    row = ListRow(40, separator=True)
    cb = row.add_leading(Checkbox(True))
    row.set_title("Write chapter 3")
    row.set_subtitle("45m · 86% focused")
    badge = row.add_trailing(Badge("25m"))
    more = row.add_trailing(IconButton("more", "More"), hover_only=True)
    _host(qtbot, row)
    assert badge.isVisible() and not more.isVisible()
    assert more.sizePolicy().retainSizeWhenHidden()
    row.set_selected(True)
    assert more.isVisible() and row.is_selected()
    row.set_selected(False)
    assert not more.isVisible()
    row.set_actions_pinned(True)
    assert more.isVisible()
    clicked: list[int] = []
    activated: list[int] = []
    row.clicked.connect(lambda: clicked.append(1))
    row.activated.connect(lambda: activated.append(1))
    pos = QPoint(row.width() // 2, row.height() // 2)
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton, pos=pos)
    qtbot.mouseDClick(row, Qt.MouseButton.LeftButton, pos=pos)
    assert clicked and activated == [1]
    assert cb.isVisible()


def test_settings_group(qtbot) -> None:  # type: ignore[no-untyped-def]
    g = SettingsGroup("Check-ins")
    t = Toggle(True)
    r1 = g.add("Check-ins", "Ask whether you're still on task.", t)
    r2 = g.add_row(SettingsRow("Interval", None, Select([(7, "7 min")], 7)))
    _host(qtbot, g)
    assert g.rows() == [r1, r2]
    assert r1.control is t and t.accessibleName() == "Check-ins"
    assert r2.control is not None and r2.control.accessibleName() == "Interval"  # type: ignore[attr-defined]
    r1.set_description(None)
    assert not r1.description_label.isVisible()


def test_inline_banner(qtbot) -> None:  # type: ignore[no-untyped-def]
    hits: list[str] = []
    b = InlineBanner("warning", "You were away 1h 52m", [("Resume", lambda: hits.append("r")), ("Abandon", lambda: hits.append("a"))], dismissible=True)
    _host(qtbot, b, width=800)
    assert not b.is_stacked()
    dismissed: list[int] = []
    b.dismissed.connect(lambda: dismissed.append(1))
    qtbot.mouseClick(b.action_buttons()[1], Qt.MouseButton.LeftButton)
    assert hits == ["a"]
    b.resize(260, b.height())
    QApplication.processEvents()
    assert b.is_stacked()
    b.set_text("Nudges snoozed until 14:30")
    b.set_tone("accent")
    b.dismiss()
    assert dismissed == [1] and b.isHidden()


def test_empty_state_and_page(qtbot) -> None:  # type: ignore[no-untyped-def]
    es = EmptyState("sparkle", "Small habits, big results", "Improve by 1% every day.", Button("New habit", "primary"))
    es.add_widget(Chip("Read 10 pages", checkable=False))
    page = PageScroll(760)
    header = PageHeader("Habits", [Button("New habit", "primary")], subtitle="Improve by 1% every day")
    page.add(header)
    page.add(es)
    qtbot.addWidget(page)
    _KEEP.append(page)
    page.resize(1000, 600)
    page.show()
    QApplication.processEvents()
    assert page.column.width() <= 760
    assert page.column.x() > 0  # centred
    assert not page.viewport().autoFillBackground() and not page.widget().autoFillBackground()
    header.set_subtitle(None)
    es.set_body("x")
    page.scroll_to_top()


def test_text_field_programmatic_value_shows_its_beginning(qtbot) -> None:  # type: ignore[no-untyped-def]
    """A long prefilled value (e.g. the break view's next block) starts at its first word."""
    from madhura.ui.widgets import TextField

    field = TextField("Next")
    qtbot.addWidget(field)
    field.resize(160, 40)
    title = "Refactor the onboarding flow and remove the legacy settings code"
    field.set_text(title)
    assert field.line_edit.cursorPosition() == 0
    field.set_text(title, select=True)
    assert field.line_edit.selectedText() == title and field.line_edit.cursorPosition() == 0
    field.show()
    field.line_edit.setFocus()
    qtbot.waitUntil(field.line_edit.hasFocus, timeout=1000)
    field.set_text("Typing goes on", select=False)
    assert field.line_edit.cursorPosition() == len("Typing goes on")  # editing: Qt's default
