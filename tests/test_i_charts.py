"""ui.widgets.charts / stat_card — geometry, hit-testing, hover, keyboard, a11y and painting [I]."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QHelpEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from madhura.ui import theme
from madhura.ui.widgets.charts import (
    HEATMAP_ALPHAS,
    Bar,
    BarChart,
    HBarItem,
    HBarList,
    Heatmap,
    fmt_axis,
    heatmap_hours,
    paint_callout,
)
from madhura.ui.widgets.stat_card import StatCard, delta_text


@pytest.fixture
def tm(qapp: QApplication) -> Iterator[theme.ThemeManager]:
    """A dark ThemeManager with reduce-motion on (instant animations)."""
    mgr = theme.ThemeManager("dark", "indigo", True)
    theme.set_current(mgr)
    yield mgr
    theme.set_current(None)


def _bars(values: list[float]) -> list[Bar]:
    return [Bar(v, f"D{i}", f"Day {i}", f"{int(v // 60)}m", current=i == len(values) - 1) for i, v in enumerate(values)]


def _render(widget) -> QImage:  # noqa: ANN001
    img = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    widget.render(img)
    return img


def _mouse_move(widget, pos: QPoint) -> None:  # noqa: ANN001
    """Move the pointer to *pos*. Qt drops a move to the global position the pointer already has
    (an earlier test may have left it exactly there), so step off it first."""
    QTest.mouseMove(widget, pos + QPoint(3, 3))
    QTest.mouseMove(widget, pos)


# =========================================================================== helpers


@pytest.mark.parametrize(
    ("secs", "text"),
    [
        (0, "0"), (-5, "0"), (900, "15m"), (1800, "30m"), (3600, "1h"),
        (5400, "1.5h"), (7200, "2h"), (43200, "12h"), (9000, "2.5h"),
    ],
)
def test_fmt_axis(secs: int, text: str) -> None:
    assert fmt_axis(secs) == text


def test_heatmap_hours_rule() -> None:
    m = [[0.0] * 24 for _ in range(7)]
    assert heatmap_hours(m) == (6, 23)
    m[3][5] = 30.0  # half a minute of spill-over does not widen
    assert heatmap_hours(m) == (6, 23)
    m[4][0] = 30.0  # a minute in total does
    assert heatmap_hours(m) == (0, 23)


def test_paint_callout_clamps_and_flips(tm) -> None:  # noqa: ANN001
    img = QImage(300, 120, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    bounds = QRectF(0, 0, 300, 120)
    r = paint_callout(p, QPointF(2, 10), "Tue 14 Oct", "2h 15m · 3 sessions", bounds)
    assert bounds.contains(r)
    assert r.top() >= 10  # no room above → flipped below the anchor
    r2 = paint_callout(p, QPointF(150, 100), "Tue", "1h", bounds)
    assert r2.bottom() <= 100  # above the anchor
    r3 = paint_callout(p, QPointF(150, 10), "Mon", "x", bounds, below=True)
    assert r3.top() >= 10
    p.end()


# =========================================================================== BarChart


def test_bar_chart_constructs_empty(tm) -> None:  # noqa: ANN001
    chart = BarChart()
    assert chart.bars() == [] and chart.index_at(QPointF(50, 50)) is None
    assert chart.accessibleName() == "Focus time chart"
    chart.resize(400, 200)
    _render(chart)


def test_bar_chart_ticks_goal_and_geometry(tm) -> None:  # noqa: ANN001
    chart = BarChart()
    chart.resize(536, 200)
    chart.set_data(_bars([3600, 0, 5400, 30, 2 * 3600, 900, 1800]), goal=7200)
    assert chart.ticks() == [0, 3600, 7200, 10800]
    assert chart.tick_labels() == ["0", "1h", "2h", "3h"]
    assert chart.goal() == 7200 and chart.goal_label() == "Goal 2h"
    plot = chart.plot_rect()
    assert plot.left() == BarChart.AXIS_WIDTH
    assert plot.right() < chart.width() - 30  # room for the goal label on the right
    slot = chart.slot_width()
    assert slot == pytest.approx(plot.width() / 7)
    assert chart.bar_width() == pytest.approx(min(32.0, slot * 0.6))
    r0 = chart.bar_rect(0)
    assert r0.center().x() == pytest.approx(plot.left() + slot / 2)
    assert r0.bottom() == pytest.approx(plot.bottom())
    assert r0.height() == pytest.approx(plot.height() / 3)
    r4 = chart.bar_rect(4)
    assert r4.height() == pytest.approx(plot.height() * 2 / 3)
    assert chart.bar_rect(1).height() == 0  # zero → no bar
    assert chart.bar_rect(3).height() >= 2  # tiny but non-zero stays visible
    assert chart.bar_rect(99).isNull()
    assert chart.value_to_y(7200) == pytest.approx(plot.bottom() - plot.height() * 2 / 3)


def test_bar_width_is_capped_at_32(tm) -> None:  # noqa: ANN001
    chart = BarChart()
    chart.resize(900, 200)
    chart.set_data(_bars([600, 1200]))
    assert chart.bar_width() == 32.0
    assert chart.goal() is None and chart.goal_label() == ""


def test_bar_chart_hit_testing(tm) -> None:  # noqa: ANN001
    chart = BarChart()
    chart.resize(536, 200)
    chart.set_data(_bars([3600] * 7))
    for i in range(7):
        assert chart.index_at(chart.slot_rect(i).center()) == i
    plot = chart.plot_rect()
    assert chart.index_at(QPointF(plot.left() - 1, plot.center().y())) is None
    assert chart.index_at(QPointF(plot.right() + 1, plot.center().y())) is None
    assert chart.index_at(QPointF(plot.left() + 1, plot.bottom() + 10)) == 0  # x-label area still hovers
    assert chart.index_at(QPointF(plot.left() + 1, chart.height() + 50)) is None


def test_bar_chart_mouse_hover_and_callout(tm, qtbot) -> None:  # noqa: ANN001
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.resize(536, 200)
    chart.set_data([Bar(8100, "Tue", "Tue 14 Oct", "2h 15m · 3 sessions", True)] + _bars([600] * 6))
    chart.show()
    seen: list[int] = []
    chart.hovered.connect(seen.append)
    _mouse_move(chart, chart.slot_rect(0).center().toPoint())
    assert chart.hover_index == 0 and seen[-1] == 0
    assert chart.callout_text() == "Tue 14 Oct · 2h 15m · 3 sessions"
    img = _render(chart)  # callout painted
    assert not img.isNull()
    QApplication.sendEvent(chart, QEvent(QEvent.Type.Leave))
    assert chart.hover_index == -1 and seen[-1] == -1
    assert chart.callout_text() == ""


def test_bar_chart_keyboard(tm, qtbot) -> None:  # noqa: ANN001
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.resize(400, 200)
    chart.set_data(_bars([60, 120, 180, 240]))
    chart.show()
    assert chart.focusPolicy() == Qt.FocusPolicy.TabFocus
    chart.activateWindow()
    qtbot.waitUntil(chart.isActiveWindow, timeout=2000)
    chart.clearFocus()
    chart.setFocus(Qt.FocusReason.OtherFocusReason)
    QApplication.processEvents()
    assert chart.hover_index == -1  # only keyboard focus highlights
    chart.clearFocus()
    chart.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert chart.hover_index == 3  # focus highlights the latest bar
    QTest.keyClick(chart, Qt.Key.Key_Left)
    assert chart.hover_index == 2
    QTest.keyClick(chart, Qt.Key.Key_Home)
    assert chart.hover_index == 0
    QTest.keyClick(chart, Qt.Key.Key_Left)
    assert chart.hover_index == 0
    QTest.keyClick(chart, Qt.Key.Key_End)
    assert chart.hover_index == 3
    QTest.keyClick(chart, Qt.Key.Key_Right)
    assert chart.hover_index == 3


def test_bar_chart_accessible_description(tm) -> None:  # noqa: ANN001
    chart = BarChart()
    chart.set_data(_bars([3600, 7200]), goal=3600)
    desc = chart.accessibleDescription()
    assert "2 bars" in desc and "total 3h" in desc and "Highest: Day 1, 2h" in desc and "met 2 of 2" in desc
    chart.setAccessibleDescription("Custom summary.")
    chart.set_data(_bars([60]))
    assert chart.accessibleDescription() == "Custom summary."


def test_bar_chart_animates_from_previous_heights(qapp, qtbot) -> None:  # noqa: ANN001
    mgr = theme.ThemeManager("dark", "indigo", False)
    theme.set_current(mgr)
    try:
        chart = BarChart()
        qtbot.addWidget(chart)
        chart.resize(400, 200)
        chart.show()
        chart.set_data(_bars([3600, 3600]), animate=False)
        chart.set_data(_bars([7200, 7200]))
        assert chart.bar_rect(0, displayed=True).height() < chart.bar_rect(0).height()
        # The bars grow from their previous values (drawn on the new scale).
        start_h = chart.plot_rect().height() * 3600 / chart.top_value()
        assert chart.bar_rect(0, displayed=True).height() == pytest.approx(start_h, abs=1.0)
        qtbot.waitUntil(lambda: chart.bar_rect(0, displayed=True).height() == pytest.approx(chart.bar_rect(0).height()),
                        timeout=2000)
    finally:
        theme.set_current(None)


def test_bar_chart_reduce_motion_is_instant(tm, qtbot) -> None:  # noqa: ANN001
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.resize(400, 200)
    chart.show()
    chart.set_data(_bars([3600, 7200]))
    assert chart.bar_rect(1, displayed=True) == chart.bar_rect(1)


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_bar_chart_paints_current_bar_in_accent(qapp, scheme: str) -> None:  # noqa: ANN001
    mgr = theme.ThemeManager(scheme, "teal", True)
    theme.set_current(mgr)
    try:
        chart = BarChart()
        chart.resize(400, 200)
        chart.set_data(_bars([3600, 3600]))
        img = _render(chart)
        r_cur = chart.bar_rect(1)
        r_old = chart.bar_rect(0)
        accent = theme.palette().color("accent")
        cur = img.pixelColor(int(r_cur.center().x()), int(r_cur.center().y()))
        old = img.pixelColor(int(r_old.center().x()), int(r_old.center().y()))
        assert cur.name() == accent.name()  # today = 100 %
        assert old.name() != accent.name()  # others at 55 % (blended with the background)
    finally:
        theme.set_current(None)


# =========================================================================== Heatmap


def _matrix() -> list[list[float]]:
    m = [[0.0] * 24 for _ in range(7)]
    m[1][10] = 4800.0  # Tue 10:00 → 1h 20m
    m[4][15] = 600.0
    return m


def test_heatmap_geometry_and_hit_testing(tm) -> None:  # noqa: ANN001
    hm = Heatmap()
    hm.resize(420, hm.height())
    hm.set_data(_matrix())
    assert hm.hours() == (6, 23) and len(hm.columns()) == 18
    cw = hm.cell_width()
    assert cw == pytest.approx((420 - Heatmap.LABEL_WIDTH - 17 * Heatmap.GAP) / 18)
    r = hm.cell_rect(1, 10)
    assert r.height() == Heatmap.CELL_HEIGHT
    assert r.left() == pytest.approx(Heatmap.LABEL_WIDTH + 4 * (cw + Heatmap.GAP))
    assert r.top() == pytest.approx(Heatmap.CELL_HEIGHT + Heatmap.GAP)
    assert hm.cell_rect(0, 3).isNull()  # hour not shown
    assert hm.cell_at(r.center()) == (1, 10)
    for row in (0, 6):
        for hour in (6, 23):
            assert hm.cell_at(hm.cell_rect(row, hour).center()) == (row, hour)
    assert hm.cell_at(QPointF(2, 2)) is None  # weekday label column
    assert hm.cell_at(QPointF(hm.grid_rect().right() + 5, 5)) is None
    assert hm.level(1, 10) == 4 and hm.level(4, 15) == 1 and hm.level(0, 0) == 0
    assert hm.legend_rect().top() >= hm.grid_rect().bottom()


def test_heatmap_cells_cap_width_and_widen(tm) -> None:  # noqa: ANN001
    hm = Heatmap()
    hm.resize(2000, hm.height())
    m = _matrix()
    m[0][2] = 60.0
    hm.set_data(m)
    assert hm.hours() == (0, 23) and len(hm.columns()) == 24
    assert hm.cell_width() == Heatmap.MAX_CELL_WIDTH
    hm.set_data(_matrix(), hours=(8, 20))
    assert hm.columns()[0] == 8 and hm.columns()[-1] == 20


def test_heatmap_level_colors(tm) -> None:  # noqa: ANN001
    hm = Heatmap()
    pal = theme.palette()
    assert hm.level_color(0).name() == pal.color("surface_alt").name()
    assert hm.level_color(4).name() == pal.color("accent").name()
    assert len(HEATMAP_ALPHAS) == 4 and HEATMAP_ALPHAS[0] == 0.2 and HEATMAP_ALPHAS[-1] == 1.0
    # Levels get progressively closer to the accent.
    acc = pal.color("accent")
    dist = [abs(hm.level_color(i).red() - acc.red()) + abs(hm.level_color(i).blue() - acc.blue()) for i in range(5)]
    assert dist == sorted(dist, reverse=True)


def test_heatmap_hover_callout_and_keyboard(tm, qtbot) -> None:  # noqa: ANN001
    hm = Heatmap()
    qtbot.addWidget(hm)
    hm.resize(420, hm.height())
    hm.set_data(_matrix(), use_24h=True)
    hm.show()
    _mouse_move(hm, hm.cell_rect(1, 10).center().toPoint())
    assert hm.hover_cell == (1, 10)
    assert hm.callout_text() == "Tue 10:00–11:00 · 1h 20m focused"
    assert hm.callout_text((0, 6)) == "Mon 06:00–07:00 · No focus"
    _render(hm)
    QApplication.sendEvent(hm, QEvent(QEvent.Type.Leave))
    assert hm.hover_cell is None
    hm.activateWindow()
    qtbot.waitUntil(hm.isActiveWindow, timeout=2000)
    hm.clearFocus()
    hm.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert hm.hover_cell == (1, 10)  # focus jumps to the busiest cell
    QTest.keyClick(hm, Qt.Key.Key_Down)
    assert hm.hover_cell == (2, 10)
    QTest.keyClick(hm, Qt.Key.Key_Right)
    assert hm.hover_cell == (2, 11)
    for _ in range(30):
        QTest.keyClick(hm, Qt.Key.Key_Left)
    assert hm.hover_cell == (2, 6)


def test_heatmap_12h_labels(tm) -> None:  # noqa: ANN001
    hm = Heatmap()
    hm.set_data(_matrix(), use_24h=False)
    assert hm.callout_text((1, 13)) == "Tue 1:00 PM–2:00 PM · No focus"
    assert hm._hour_label(15) == "3p" and hm._hour_label(9) == "9a"  # noqa: SLF001


def test_heatmap_not_enough_data(tm, qtbot) -> None:  # noqa: ANN001
    hm = Heatmap()
    qtbot.addWidget(hm)
    hm.resize(420, hm.height())
    hm.set_data(_matrix(), enough=False)
    hm.show()
    assert not hm.has_enough_data()
    _mouse_move(hm, hm.cell_rect(1, 10).center().toPoint())
    assert hm.hover_cell is None
    assert hm.accessibleDescription() == "Patterns appear after a few sessions."
    _render(hm)


def test_heatmap_description(tm) -> None:  # noqa: ANN001
    hm = Heatmap()
    hm.set_data(_matrix())
    desc = hm.accessibleDescription()
    assert "Tuesdays around 10:00–11:00" in desc and "1h 20m" in desc and "Busiest weekday: Tuesday" in desc
    hm.set_data([[0.0] * 24 for _ in range(7)])
    assert hm.accessibleDescription() == "No focus recorded in this range."


# =========================================================================== HBarList


def test_hbar_list_layout_and_lengths(tm) -> None:  # noqa: ANN001
    lst = HBarList()
    lst.resize(300, 100)
    assert lst.height() == HBarList.EMPTY_HEIGHT
    lst.set_items([HBarItem("Madhura", 300, "5m", "#ff0000", "tip"), HBarItem("Thesis", 150, "2m"), HBarItem("Zero", 0, "0m")])
    assert lst.height() == 3 * HBarList.ROW_HEIGHT - 6
    assert lst.bar_rect(0).width() == pytest.approx(300)
    assert lst.bar_rect(1).width() == pytest.approx(150)
    assert lst.bar_rect(2).width() == 0
    assert lst.bar_rect(9).isNull()
    assert lst.index_at(QPointF(10, 5)) == 0
    assert lst.index_at(QPointF(10, HBarList.ROW_HEIGHT + 5)) == 1
    assert lst.index_at(QPointF(10, 500)) is None
    assert lst.accessibleDescription() == "Madhura: 5m; Thesis: 2m; Zero: 0m."
    _render(lst)


def test_hbar_list_empty_text_and_tooltip(tm, qtbot) -> None:  # noqa: ANN001
    lst = HBarList()
    qtbot.addWidget(lst)
    lst.set_empty_text("Nothing yet.")
    assert lst.accessibleDescription() == "Nothing yet." and lst.empty_text() == "Nothing yet."
    _render(lst)
    lst.resize(300, 100)
    lst.set_items([HBarItem("Madhura", 300, "5m", None, "Madhura · 5m")])
    lst.show()
    ev = QHelpEvent(QEvent.Type.ToolTip, QPoint(10, 5), lst.mapToGlobal(QPoint(10, 5)))
    assert lst.event(ev) is True
    _mouse_move(lst, QPoint(10, 5))
    assert lst._hover == 0  # noqa: SLF001
    _render(lst)


# =========================================================================== StatCard


@pytest.mark.parametrize(
    ("delta", "unit", "text", "tone"),
    [
        (0.12, "%", "▲ 12%", "success"),
        (-0.08, "%", "▼ 8%", "muted"),
        (None, "%", "—", "muted"),
        (0.0, "%", "0%", "muted"),
        (0.004, "%", "0%", "muted"),
        (0.04, "pts", "▲ 4 pts", "success"),
        (-0.01, "pts", "▼ 1 pt", "muted"),
        (12.5, "%", "▲ 1,250%", "success"),
    ],
)
def test_delta_text(delta, unit: str, text: str, tone: str) -> None:  # noqa: ANN001
    assert delta_text(delta, unit) == (text, tone)


def test_stat_card_layout_and_values(tm) -> None:  # noqa: ANN001
    card = StatCard("Focus time")
    assert card.height() == 108 and card.label_text() == "Focus time"
    card.set_number(4800, lambda v: f"{int(v)}s")
    assert card.value_text() == "4800s" and card.displayed_text() == "4800s"  # reduce motion → instant
    card.set_delta(0.12, "vs previous 7 days")
    assert card.delta_text() == "▲ 12%" and card.delta_tone() == "success" and card.caption_text() == "vs previous 7 days"
    card.set_hint("Streak 5 · best 12")
    assert card.hint_text() == "Streak 5 · best 12"
    assert card.accessibleName() == "Focus time"
    assert card.accessibleDescription() == "4800s, up 12% vs previous 7 days, Streak 5 · best 12"
    card.set_number(None)
    assert card.value_text() == "—" and card.number() is None
    card.set_value("n/a")
    assert card.value_text() == "n/a"


def test_stat_card_counts_up(qapp, qtbot) -> None:  # noqa: ANN001
    mgr = theme.ThemeManager("dark", "indigo", False)
    theme.set_current(mgr)
    try:
        card = StatCard("Sessions")
        qtbot.addWidget(card)
        card.show()
        card.set_number(100, lambda v: str(int(round(v))))
        assert card.value_text() == "100"
        assert card.displayed_text() != "100"  # still counting
        qtbot.waitUntil(lambda: card.displayed_text() == "100", timeout=2000)
        card.set_number(100)
        assert card.displayed_text() == "100"
    finally:
        theme.set_current(None)
