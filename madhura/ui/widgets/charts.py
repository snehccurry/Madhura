"""Custom-painted, theme-aware charts for Insights (spec §1.9): :class:`BarChart`,
:class:`Heatmap` and :class:`HBarList`.

All three read their colours from ``theme.current().palette`` at paint time (so theme and accent
switches repaint without rebuilding), keep their geometry maths in small public helpers that tests
can call (``bar_rect``, ``index_at``, ``cell_rect``, ``cell_at``, ``row_rect``), and expose a
plain-text summary through ``accessibleDescription`` (set by the caller or built from the data).

Hover feedback is painted *inside* the widget as a small callout (no tooltip window), so it also
shows up in screenshots and never steals focus. The bar and heatmap charts are keyboard
accessible: Tab focuses them and ←/→ (↑/↓ on the heatmap) move the highlighted item.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFontMetricsF,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from madhura.core.timefmt import WEEKDAYS_SHORT, fmt_time
from madhura.ui import animations, fonts, theme
from madhura.ui.widgets._common import KeyboardFocusMixin, fill_rounded
from madhura.ui.widgets.popup import paint_shadow

__all__ = [
    "Bar",
    "BarChart",
    "Heatmap",
    "HBarItem",
    "HBarList",
    "HEATMAP_ALPHAS",
    "fmt_axis",
    "heatmap_hours",
    "paint_callout",
]

#: Accent alpha of heatmap levels 1..4 (level 0 is ``surface_alt``).
HEATMAP_ALPHAS: tuple[float, ...] = (0.20, 0.40, 0.65, 1.0)

_TICK_FONT_PX = 11
_CALLOUT_RADIUS = 8.0
_CALLOUT_PAD_X = 10.0
_CALLOUT_PAD_Y = 6.0


# =========================================================================== helpers


def fmt_axis(seconds: float) -> str:
    """Compact axis label: ``"0"``, ``"15m"``, ``"1h"``, ``"1.5h"``, ``"12h"``."""
    secs = int(round(seconds))
    if secs <= 0:
        return "0"
    if secs < 3600:
        return f"{secs // 60}m"
    hours = secs / 3600.0
    if abs(hours - round(hours)) < 1e-6:
        return f"{int(round(hours))}h"
    return f"{hours:.1f}".rstrip("0").rstrip(".") + "h"


def heatmap_hours(matrix: Sequence[Sequence[float]]) -> tuple[int, int]:
    """Hour range shown by the heatmap: 06–23, widened to 00–23 when any minutes fall in 00–05."""
    from madhura.core.analytics import matrix_has_early_hours

    return (0, 23) if matrix_has_early_hours(matrix) else (6, 23)


def _ticks_for(top_value: float, count: int = 4) -> list[int]:
    from madhura.core.analytics import nice_ticks

    return nice_ticks(top_value, count)


def _connect_theme(slot: Callable[[], None]) -> None:
    theme.current().changed.connect(slot)


def paint_callout(
    painter: QPainter,
    anchor: QPointF,
    title: str,
    detail: str,
    bounds: QRectF,
    *,
    below: bool = False,
    gap: float = 8.0,
) -> QRectF:
    """Paint a hover callout ("**Tue 14 Oct** · 2h 15m · 3 sessions") centred on *anchor*.

    The bubble sits *gap* px above the anchor (or below with ``below=True``), is clamped inside
    *bounds* and flips to the other side when it would not fit. Returns the bubble rect.
    """
    pal = theme.palette()
    bold = fonts.font(12, 600)
    regular = fonts.font(12, 400, tabular=True)
    fm_b = QFontMetricsF(bold)
    fm_r = QFontMetricsF(regular)
    sep = " · " if title and detail else ""
    w_title = fm_b.horizontalAdvance(title)
    w_rest = fm_r.horizontalAdvance(sep + detail)
    width = w_title + w_rest + 2 * _CALLOUT_PAD_X + 4.0  # a little slack for hinting differences
    height = max(fm_b.height(), fm_r.height()) + 2 * _CALLOUT_PAD_Y
    width = min(width, max(40.0, bounds.width()))
    x = anchor.x() - width / 2.0
    x = max(bounds.left(), min(x, bounds.right() - width))
    y_above = anchor.y() - gap - height
    y_below = anchor.y() + gap
    if below:
        y = y_below if y_below + height <= bounds.bottom() else y_above
    else:
        y = y_above if y_above >= bounds.top() else y_below
    y = max(bounds.top(), min(y, bounds.bottom() - height))
    rect = QRectF(round(x), round(y), round(width), round(height))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    margin = 10
    paint_shadow(painter, rect.adjusted(-margin, -margin, margin, margin), _CALLOUT_RADIUS, margin, blur=14.0, offset_y=4.0,
                 alpha=0.5 if pal.is_dark else 0.16)
    bg = pal.surface_alt if pal.is_dark else pal.surface
    fill_rounded(painter, rect, _CALLOUT_RADIUS, bg, pal.border_strong if pal.is_dark else pal.border)
    text_rect = rect.adjusted(_CALLOUT_PAD_X, 0, -_CALLOUT_PAD_X, 0)
    painter.setFont(bold)
    painter.setPen(QColor(pal.text))
    title_w = min(w_title, text_rect.width())
    painter.drawText(QRectF(text_rect.left(), text_rect.top(), title_w, text_rect.height()),
                     int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), title)
    if sep + detail:
        painter.setFont(regular)
        painter.setPen(QColor(pal.text_muted))
        rest = QRectF(text_rect.left() + title_w, text_rect.top(), max(0.0, text_rect.width() - title_w), text_rect.height())
        shown = sep + detail
        if w_rest > rest.width() + 0.5:
            shown = fm_r.elidedText(shown, Qt.TextElideMode.ElideRight, rest.width())
        painter.drawText(rest, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), shown)
    painter.restore()
    return rect


def _top_rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rect with only the two top corners rounded."""
    r = max(0.0, min(radius, rect.width() / 2.0, rect.height()))
    path = QPainterPath()
    if r <= 0.01:
        path.addRect(rect)
        return path
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    path.moveTo(left, bottom)
    path.lineTo(left, top + r)
    path.quadTo(left, top, left + r, top)
    path.lineTo(right - r, top)
    path.quadTo(right, top, right, top + r)
    path.lineTo(right, bottom)
    path.closeSubpath()
    return path


# =========================================================================== BarChart


@dataclass(frozen=True)
class Bar:
    """One bar: ``value`` in seconds, the x-axis ``label`` ("" = none), the callout ``title``
    ("Tue 14 Oct") and ``detail`` ("2h 15m · 3 sessions"); ``current`` = today / this week."""

    value: float
    label: str = ""
    title: str = ""
    detail: str = ""
    current: bool = False


class BarChart(KeyboardFocusMixin, QWidget):
    """Vertical bars of focused time with a dashed goal line (spec §1.9).

    y axis 36 px with 4 nice ticks in hours; bars ``min(32, slot × 0.6)`` wide with 4 px rounded
    tops, accent at 55 % (100 % for the current day/week and the hovered bar); hover paints a
    callout. ``set_data`` animates bars from their previous heights (instant with reduce-motion).
    """

    hovered = Signal(int)

    AXIS_WIDTH = 36
    X_LABEL_HEIGHT = 22
    TOP_PAD = 10
    RIGHT_PAD = 4
    MAX_BAR_WIDTH = 32.0
    BAR_RADIUS = 4.0
    BASE_ALPHA = 0.55

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bars: list[Bar] = []
        self._from: list[float] = []
        self._goal: float | None = None
        self._goal_label = ""
        self._ticks: list[int] = _ticks_for(0)
        self._hover = -1
        self._progress = 1.0
        self._auto_description = True
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(140)
        self._ring_radius = 8.0
        self.setAccessibleName("Focus time chart")
        _connect_theme(self.update)

    # ------------------------------------------------------------------ data
    def set_data(
        self,
        bars: Sequence[Bar],
        goal: float | None = None,
        goal_label: str | None = None,
        *,
        animate: bool = True,
    ) -> None:
        """Replace the bars (values in seconds) and the goal line (None/0 = no line)."""
        old = self._displayed_values()
        self._bars = list(bars)
        self._goal = float(goal) if goal and goal > 0 else None
        self._goal_label = goal_label if goal_label is not None else (f"Goal {fmt_axis(self._goal)}" if self._goal else "")
        top = max([b.value for b in self._bars] + [self._goal or 0.0, 0.0])
        self._ticks = _ticks_for(top)
        self._from = old if len(old) == len(self._bars) else [0.0] * len(self._bars)
        if self._hover >= len(self._bars):
            self._hover = -1
        if self._auto_description:
            QWidget.setAccessibleDescription(self, self.default_description())
        self._anim.stop()
        if animate and animations.motion_enabled() and self._bars and self.isVisible():
            self._progress = 0.0
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        else:
            self._progress = 1.0
        self.update()

    def bars(self) -> list[Bar]:
        """The current bars."""
        return list(self._bars)

    def goal(self) -> float | None:
        """Goal line value in seconds (None when hidden)."""
        return self._goal

    def goal_label(self) -> str:
        """Text drawn next to the goal line ("Goal 2h")."""
        return self._goal_label

    def ticks(self) -> list[int]:
        """y-axis tick values in seconds (4 values, starting at 0)."""
        return list(self._ticks)

    def tick_labels(self) -> list[str]:
        """y-axis tick labels ("0", "1h", "2h", "3h")."""
        return [fmt_axis(t) for t in self._ticks]

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        """Use *text* as the chart summary (stops the automatic one)."""
        self._auto_description = not text
        super().setAccessibleDescription(text or self.default_description())

    def default_description(self) -> str:
        """Plain-text summary built from the bars."""
        if not self._bars:
            return "No data."
        total = sum(b.value for b in self._bars)
        best = max(self._bars, key=lambda b: b.value)
        parts = [f"{len(self._bars)} bars, total {_fmt_long(total)}."]
        if best.value > 0:
            parts.append(f"Highest: {best.title or best.label}, {_fmt_long(best.value)}.")
        if self._goal:
            met = sum(1 for b in self._bars if b.value >= self._goal)
            parts.append(f"Goal {_fmt_long(self._goal)} met {met} of {len(self._bars)} times.")
        return " ".join(parts)

    # ------------------------------------------------------------------ geometry
    def _goal_gutter(self) -> float:
        if not self._goal:
            return float(self.RIGHT_PAD)
        fm = QFontMetricsF(fonts.font(_TICK_FONT_PX, 500))
        return fm.horizontalAdvance(self._goal_label) + 14.0

    def plot_rect(self) -> QRectF:
        """Area the bars are drawn in (widget coordinates)."""
        left = float(self.AXIS_WIDTH)
        top = float(self.TOP_PAD)
        right = self.width() - self._goal_gutter()
        bottom = self.height() - float(self.X_LABEL_HEIGHT)
        return QRectF(left, top, max(1.0, right - left), max(1.0, bottom - top))

    def slot_width(self) -> float:
        """Horizontal space per bar."""
        n = max(1, len(self._bars))
        return self.plot_rect().width() / n

    def bar_width(self) -> float:
        """Bar width: ``min(32, slot × 0.6)``."""
        return min(self.MAX_BAR_WIDTH, self.slot_width() * 0.6)

    def top_value(self) -> float:
        """Value at the top of the plot (the last tick)."""
        return float(self._ticks[-1]) if self._ticks and self._ticks[-1] > 0 else 1.0

    def value_to_y(self, value: float) -> float:
        """y coordinate of *value* seconds."""
        plot = self.plot_rect()
        frac = max(0.0, min(1.0, float(value) / self.top_value()))
        return plot.bottom() - frac * plot.height()

    def slot_rect(self, index: int) -> QRectF:
        """Full-height hit area of bar *index*."""
        plot = self.plot_rect()
        slot = self.slot_width()
        return QRectF(plot.left() + slot * index, plot.top(), slot, plot.height())

    def bar_rect(self, index: int, *, displayed: bool = False) -> QRectF:
        """Rect of bar *index* at its final height (or the animated one with ``displayed``).

        A non-zero value is always at least 2 px tall; a zero value has an empty rect.
        """
        if not 0 <= index < len(self._bars):
            return QRectF()
        plot = self.plot_rect()
        slot = self.slot_width()
        w = self.bar_width()
        x = plot.left() + slot * index + (slot - w) / 2.0
        value = self._displayed_values()[index] if displayed else self._bars[index].value
        if value <= 0:
            return QRectF(x, plot.bottom(), w, 0.0)
        h = max(2.0, plot.bottom() - self.value_to_y(value))
        return QRectF(x, plot.bottom() - h, w, h)

    def index_at(self, pos: QPointF) -> int | None:
        """Index of the bar whose slot contains *pos* (None outside the plot)."""
        if not self._bars:
            return None
        plot = self.plot_rect()
        p = QPointF(pos)
        in_x = plot.left() <= p.x() < plot.right()
        in_y = plot.top() - self.TOP_PAD <= p.y() <= plot.bottom() + self.X_LABEL_HEIGHT
        if not (in_x and in_y):
            return None
        i = int((p.x() - plot.left()) // self.slot_width())
        return i if 0 <= i < len(self._bars) else None

    # ------------------------------------------------------------------ hover
    @property
    def hover_index(self) -> int:
        """Highlighted bar (-1 = none)."""
        return self._hover

    def set_hover_index(self, index: int | None) -> None:
        """Highlight bar *index* (None/-1 clears)."""
        i = -1 if index is None or not 0 <= index < len(self._bars) else int(index)
        if i == self._hover:
            return
        self._hover = i
        self.hovered.emit(i)
        self.update()

    def callout_text(self, index: int | None = None) -> str:
        """Callout of bar *index* (default: hovered) as plain text."""
        i = self._hover if index is None else index
        if not 0 <= i < len(self._bars):
            return ""
        b = self._bars[i]
        return " · ".join(t for t in (b.title, b.detail) if t)

    # ------------------------------------------------------------------ animation
    def _on_anim(self, value: object) -> None:
        self._progress = float(value)  # type: ignore[arg-type]
        self.update()

    def _displayed_values(self) -> list[float]:
        t = self._progress
        if t >= 1.0 or len(self._from) != len(self._bars):
            return [b.value for b in self._bars]
        return [f + (b.value - f) * t for f, b in zip(self._from, self._bars, strict=False)]

    # ------------------------------------------------------------------ events
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.set_hover_index(self.index_at(event.position()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if not (self._kbd_focus and self.hasFocus()):
            self.set_hover_index(None)
        super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        if self._kbd_focus and self._hover < 0 and self._bars:
            self.set_hover_index(len(self._bars) - 1)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if not self.underMouse():
            self.set_hover_index(None)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        n = len(self._bars)
        key = event.key()
        if n and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            cur = self._hover if self._hover >= 0 else n - 1
            if key == Qt.Key.Key_Left:
                cur = max(0, cur - 1)
            elif key == Qt.Key.Key_Right:
                cur = min(n - 1, cur + 1)
            elif key == Qt.Key.Key_Home:
                cur = 0
            else:
                cur = n - 1
            self.set_hover_index(cur)
            event.accept()
            return
        super().keyPressEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(480, 200)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, 140)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot = self.plot_rect()
        tick_font = fonts.font(_TICK_FONT_PX, 500, tabular=True)
        fm = QFontMetricsF(tick_font)
        p.setFont(tick_font)

        # Hovered slot: a soft column behind the bar (also marks empty days).
        if 0 <= self._hover < len(self._bars):
            slot = self.slot_rect(self._hover)
            w = min(slot.width() - 4.0, self.bar_width() + 16.0)
            column = QRectF(slot.center().x() - w / 2.0, plot.top() - 4.0, w, plot.height() + 4.0)
            fill_rounded(p, column, 6.0, theme.with_alpha(pal.hover, 0.55 if pal.is_dark else 0.8))

        # Grid + y labels.
        top = self.top_value()
        for i, tick in enumerate(self._ticks):
            y = round(plot.bottom() - (tick / top) * plot.height()) + 0.5
            color = QColor(pal.border_strong) if i == 0 else QColor(pal.border)
            p.setPen(QPen(color, 1.0))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(pal.text_subtle))
            label_rect = QRectF(0, y - fm.height() / 2.0, self.AXIS_WIDTH - 8.0, fm.height())
            p.drawText(label_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), fmt_axis(tick))

        # Bars.
        values = self._displayed_values()
        accent = QColor(pal.accent)
        for i, bar in enumerate(self._bars):
            if values[i] <= 0:
                continue
            rect = self.bar_rect(i, displayed=True)
            alpha = 1.0 if (bar.current or i == self._hover) else self.BASE_ALPHA
            color = QColor(accent)
            color.setAlphaF(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPath(_top_rounded_path(rect, self.BAR_RADIUS))

        # Goal line.
        if self._goal:
            y = round(self.value_to_y(self._goal)) + 0.5
            pen = QPen(QColor(pal.text_subtle), 1.0)
            pen.setDashPattern([4.0, 4.0])
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(pal.text_muted))
            gl_rect = QRectF(plot.right() + 8.0, y - fm.height() / 2.0, self.width() - plot.right() - 8.0, fm.height())
            p.drawText(gl_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._goal_label)

        # x labels.
        label_top = plot.bottom() + 6.0
        for i, bar in enumerate(self._bars):
            if not bar.label:
                continue
            slot = self.slot_rect(i)
            emphasised = bar.current or i == self._hover
            p.setPen(QColor(pal.text if emphasised else pal.text_subtle))
            w = max(slot.width(), fm.horizontalAdvance(bar.label) + 4.0)
            r = QRectF(slot.center().x() - w / 2.0, label_top, w, fm.height())
            r.moveLeft(max(0.0, min(r.left(), self.width() - r.width())))
            p.drawText(r, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), bar.label)

        # Hover callout.
        if 0 <= self._hover < len(self._bars):
            bar = self._bars[self._hover]
            rect = self.bar_rect(self._hover, displayed=True)
            slot = self.slot_rect(self._hover)
            anchor_y = rect.top() if rect.height() > 0 else plot.bottom()
            bounds = QRectF(self.rect()).adjusted(0, 0, 0, -2)
            paint_callout(p, QPointF(slot.center().x(), anchor_y), bar.title, bar.detail, bounds)
        p.end()


def _fmt_long(seconds: float) -> str:
    from madhura.core.timefmt import fmt_duration

    return fmt_duration(seconds)


# =========================================================================== Heatmap


class Heatmap(KeyboardFocusMixin, QWidget):
    """Weekday × hour grid of focused time ("When you focus best", spec §1.9).

    7 rows Mon–Sun × hours 06–23 (00–23 when any focus falls in 00–05), ~16 px cells (radius 3,
    gap 3) that stretch to fill the width, x labels every 3 h, 5 quantized levels (surface_alt,
    accent 20/40/65/100 %) and a "Less ▢▢▢▢▢ More" legend. With too little data the grid is
    dimmed behind "Patterns appear after a few sessions."
    """

    hovered = Signal(int, int)

    LABEL_WIDTH = 34
    CELL_HEIGHT = 16.0
    GAP = 3.0
    RADIUS = 3.0
    X_LABEL_HEIGHT = 20
    LEGEND_HEIGHT = 26
    LEGEND_SWATCH = 10.0
    MAX_CELL_WIDTH = 40.0
    EMPTY_TEXT = "Patterns appear after a few sessions."

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._matrix: list[list[float]] = [[0.0] * 24 for _ in range(7)]
        self._levels: list[list[int]] = [[0] * 24 for _ in range(7)]
        self._hours: tuple[int, int] = (6, 23)
        self._enough = True
        self._use_24h = True
        self._hover: tuple[int, int] | None = None
        self._auto_description = True
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._content_height())
        self._ring_radius = 8.0
        self.setAccessibleName("Focus by weekday and hour")
        _connect_theme(self.update)

    # ------------------------------------------------------------------ data
    def set_data(
        self,
        matrix: Sequence[Sequence[float]],
        *,
        enough: bool = True,
        use_24h: bool = True,
        hours: tuple[int, int] | None = None,
    ) -> None:
        """7×24 focused seconds (Mon..Sun × 0..23). ``enough=False`` shows the "few sessions" note."""
        from madhura.core.analytics import heatmap_levels

        self._matrix = [[float(v) for v in list(row)[:24]] + [0.0] * max(0, 24 - len(row)) for row in list(matrix)[:7]]
        while len(self._matrix) < 7:
            self._matrix.append([0.0] * 24)
        self._levels = heatmap_levels(self._matrix)
        self._hours = hours if hours is not None else heatmap_hours(self._matrix)
        self._enough = bool(enough)
        self._use_24h = bool(use_24h)
        self._hover = None
        if self._auto_description:
            QWidget.setAccessibleDescription(self, self.default_description())
        self.update()

    def hours(self) -> tuple[int, int]:
        """First and last hour column shown."""
        return self._hours

    def columns(self) -> list[int]:
        """Hours shown, left to right."""
        return list(range(self._hours[0], self._hours[1] + 1))

    def level(self, row: int, hour: int) -> int:
        """Quantized level 0..4 of a cell."""
        return self._levels[row][hour]

    def value(self, row: int, hour: int) -> float:
        """Focused seconds of a cell."""
        return self._matrix[row][hour]

    def has_enough_data(self) -> bool:
        """False while the "Patterns appear…" note is shown."""
        return self._enough

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        """Use *text* as the summary (stops the automatic one)."""
        self._auto_description = not text
        super().setAccessibleDescription(text or self.default_description())

    def default_description(self) -> str:
        """Plain-text summary: the busiest cell and weekday."""
        if not self._enough:
            return self.EMPTY_TEXT
        best: tuple[float, int, int] | None = None
        for r, row in enumerate(self._matrix):
            for h, v in enumerate(row):
                if v > 0 and (best is None or v > best[0]):
                    best = (v, r, h)
        if best is None:
            return "No focus recorded in this range."
        day_totals = [sum(row) for row in self._matrix]
        busiest = max(range(7), key=lambda r: day_totals[r])
        v, r, h = best
        return (
            f"You focus most on {_WEEKDAYS_LONG[r]}s around {self._range_label(h)} "
            f"({_fmt_long(v)} in this range). Busiest weekday: {_WEEKDAYS_LONG[busiest]}."
        )

    # ------------------------------------------------------------------ geometry
    def _content_height(self) -> int:
        grid = 7 * self.CELL_HEIGHT + 6 * self.GAP
        return int(grid + self.X_LABEL_HEIGHT + self.LEGEND_HEIGHT)

    def cell_width(self) -> float:
        """Width of one cell (stretches with the widget, ≥ the cell height, ≤ 40 px)."""
        n = len(self.columns())
        avail = self.width() - self.LABEL_WIDTH - (n - 1) * self.GAP
        return max(8.0, min(self.MAX_CELL_WIDTH, avail / max(1, n)))

    def grid_rect(self) -> QRectF:
        """Bounding rect of the cells."""
        n = len(self.columns())
        w = n * self.cell_width() + (n - 1) * self.GAP
        h = 7 * self.CELL_HEIGHT + 6 * self.GAP
        return QRectF(float(self.LABEL_WIDTH), 0.0, w, h)

    def cell_rect(self, row: int, hour: int) -> QRectF:
        """Rect of the cell for weekday *row* (0 = Mon) and clock *hour* (empty if not shown)."""
        cols = self.columns()
        if hour not in cols or not 0 <= row < 7:
            return QRectF()
        c = cols.index(hour)
        cw = self.cell_width()
        return QRectF(self.LABEL_WIDTH + c * (cw + self.GAP), row * (self.CELL_HEIGHT + self.GAP), cw, self.CELL_HEIGHT)

    def cell_at(self, pos: QPointF) -> tuple[int, int] | None:
        """``(row, hour)`` of the cell under *pos* (gaps snap to the nearest cell; None outside)."""
        grid = self.grid_rect()
        p = QPointF(pos)
        if not grid.contains(p):
            return None
        cw = self.cell_width()
        col = int((p.x() - grid.left()) // (cw + self.GAP))
        row = int((p.y() - grid.top()) // (self.CELL_HEIGHT + self.GAP))
        cols = self.columns()
        col = max(0, min(len(cols) - 1, col))
        row = max(0, min(6, row))
        return row, cols[col]

    def legend_rect(self) -> QRectF:
        """Area of the "Less ▢▢▢▢▢ More" legend."""
        grid = self.grid_rect()
        return QRectF(0.0, grid.bottom() + self.X_LABEL_HEIGHT, float(self.width()), float(self.LEGEND_HEIGHT))

    def _hour_label(self, hour: int) -> str:
        if self._use_24h:
            return f"{hour:02d}"
        h12 = hour % 12 or 12
        return f"{h12}{'a' if hour < 12 else 'p'}"

    def _range_label(self, hour: int) -> str:
        from datetime import datetime

        start = datetime(2000, 1, 1, hour)
        end = datetime(2000, 1, 1, (hour + 1) % 24)
        return f"{fmt_time(start, self._use_24h)}–{fmt_time(end, self._use_24h)}"

    def level_color(self, level: int) -> QColor:
        """Fill colour of a level (0 = surface_alt; 1..4 = accent at 20/40/65/100 %)."""
        pal = theme.palette()
        if level <= 0:
            return QColor(pal.surface_alt)
        return theme.mix(pal.surface_alt, pal.accent, HEATMAP_ALPHAS[min(4, level) - 1])

    # ------------------------------------------------------------------ hover
    @property
    def hover_cell(self) -> tuple[int, int] | None:
        """Highlighted ``(row, hour)``."""
        return self._hover

    def set_hover_cell(self, cell: tuple[int, int] | None) -> None:
        """Highlight a cell (None clears). Ignored while the "few sessions" note is shown."""
        if not self._enough:
            cell = None
        if cell is not None and cell[1] not in self.columns():
            cell = None
        if cell == self._hover:
            return
        self._hover = cell
        if cell is not None:
            self.hovered.emit(cell[0], cell[1])
        else:
            self.hovered.emit(-1, -1)
        self.update()

    def callout_text(self, cell: tuple[int, int] | None = None) -> str:
        """Callout of *cell* (default: hovered) as plain text."""
        c = self._hover if cell is None else cell
        if c is None:
            return ""
        title, detail = self._callout_parts(c)
        return f"{title} · {detail}"

    def _callout_parts(self, cell: tuple[int, int]) -> tuple[str, str]:
        row, hour = cell
        v = self._matrix[row][hour]
        detail = f"{_fmt_long(v)} focused" if v >= 60 else ("under a minute" if v > 0 else "No focus")
        return f"{WEEKDAYS_SHORT[row]} {self._range_label(hour)}", detail

    # ------------------------------------------------------------------ events
    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.set_hover_cell(self.cell_at(event.position()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if not (self._kbd_focus and self.hasFocus()):
            self.set_hover_cell(None)
        super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        if self._kbd_focus and self._hover is None and self._enough:
            best = max(
                ((r, h) for r in range(7) for h in self.columns()),
                key=lambda c: self._matrix[c[0]][c[1]],
                default=None,
            )
            self.set_hover_cell(best)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if not self.underMouse():
            self.set_hover_cell(None)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        moves = {Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1), Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0)}
        if key in moves and self._enough:
            cols = self.columns()
            row, hour = self._hover if self._hover is not None else (0, cols[0])
            dr, dc = moves[key]
            c = max(0, min(len(cols) - 1, cols.index(hour) + dc))
            self.set_hover_cell((max(0, min(6, row + dr)), cols[c]))
            event.accept()
            return
        super().keyPressEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.LABEL_WIDTH + 18 * 19, self._content_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.LABEL_WIDTH + 24 * 9, self._content_height())

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        label_font = fonts.font(_TICK_FONT_PX, 500, tabular=True)
        fm = QFontMetricsF(label_font)
        p.setFont(label_font)
        grid = self.grid_rect()
        dim = not self._enough

        # Weekday labels.
        for row in range(7):
            r = self.cell_rect(row, self.columns()[0])
            p.setPen(QColor(pal.text_subtle))
            p.drawText(QRectF(0, r.top(), self.LABEL_WIDTH - 8.0, r.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), WEEKDAYS_SHORT[row])

        # Cells.
        p.save()
        if dim:
            p.setOpacity(0.45)
        for row in range(7):
            for hour in self.columns():
                level = 0 if dim else self._levels[row][hour]
                fill_rounded(p, self.cell_rect(row, hour), self.RADIUS, self.level_color(level))
        p.restore()

        # x labels every 3 h.
        label_y = grid.bottom() + 5.0
        p.setPen(QColor(pal.text_subtle))
        for hour in self.columns():
            if hour % 3:
                continue
            r = self.cell_rect(0, hour)
            text = self._hour_label(hour)
            w = fm.horizontalAdvance(text) + 2.0
            p.drawText(QRectF(r.left(), label_y, max(w, r.width()), fm.height()),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), text)

        # Legend (right-aligned): Less ▢▢▢▢▢ More
        legend = self.legend_rect()
        cap_font = fonts.font(_TICK_FONT_PX, 500)
        cfm = QFontMetricsF(cap_font)
        p.setFont(cap_font)
        sw = self.LEGEND_SWATCH
        more_w = cfm.horizontalAdvance("More")
        less_w = cfm.horizontalAdvance("Less")
        total = less_w + 6 + 5 * sw + 4 * 3 + 6 + more_w
        x = max(0.0, grid.right() - total)
        cy = legend.top() + legend.height() / 2.0 + 2.0
        p.setPen(QColor(pal.text_subtle))
        p.drawText(QRectF(x, cy - cfm.height() / 2.0, less_w, cfm.height()), int(Qt.AlignmentFlag.AlignVCenter), "Less")
        x += less_w + 6
        for level in range(5):
            fill_rounded(p, QRectF(x, cy - sw / 2.0, sw, sw), 2.5, self.level_color(level))
            x += sw + 3
        x += 3
        p.setPen(QColor(pal.text_subtle))
        p.drawText(QRectF(x, cy - cfm.height() / 2.0, more_w + 2, cfm.height()), int(Qt.AlignmentFlag.AlignVCenter), "More")

        # "Too little data" note.
        if dim:
            note_font = fonts.font(12, 500)
            nfm = QFontMetricsF(note_font)
            w = nfm.horizontalAdvance(self.EMPTY_TEXT) + 28
            h = nfm.height() + 14
            box = QRectF(grid.center().x() - w / 2.0, grid.center().y() - h / 2.0, w, h)
            box.moveLeft(max(0.0, min(box.left(), self.width() - w)))
            fill_rounded(p, box, h / 2.0, pal.surface, pal.border_strong if pal.is_dark else pal.border)
            p.setFont(note_font)
            p.setPen(QColor(pal.text_muted))
            p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self.EMPTY_TEXT)

        # Hover: ring + callout.
        if self._hover is not None and not dim:
            row, hour = self._hover
            r = self.cell_rect(row, hour)
            if not r.isEmpty():
                p.setPen(QPen(QColor(pal.text), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-1.0, -1.0, 1.0, 1.0), self.RADIUS + 1, self.RADIUS + 1)
                title, detail = self._callout_parts(self._hover)
                below = row < 3
                anchor = QPointF(r.center().x(), r.bottom() if below else r.top())
                paint_callout(p, anchor, title, detail, QRectF(self.rect()), below=below, gap=6.0)
        p.end()


_WEEKDAYS_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# =========================================================================== HBarList


@dataclass(frozen=True)
class HBarItem:
    """One row: ``label`` (elided), ``value`` (sorting/bar length), ``value_text`` ("4h 20m"),
    optional bar ``color`` (default accent) and ``tooltip``."""

    label: str
    value: float
    value_text: str = ""
    color: str | QColor | None = None
    tooltip: str = ""


class HBarList(QWidget):
    """Ranked horizontal bars: label + value on one line, a 6 px bar underneath (spec §1.9 Top
    projects / What pulls you away). Bar lengths are relative to the largest value."""

    ROW_HEIGHT = 40
    BAR_HEIGHT = 6.0
    BAR_TOP = 25.0
    EMPTY_HEIGHT = 56

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[HBarItem] = []
        self._empty_text = "Nothing here yet."
        self._hover = -1
        self._auto_description = True
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.EMPTY_HEIGHT)
        self.setAccessibleName("Ranked list")
        _connect_theme(self.update)

    def set_items(self, items: Sequence[HBarItem]) -> None:
        """Replace the rows (shown in the given order)."""
        self._items = list(items)
        self._hover = -1
        self.setFixedHeight(len(self._items) * self.ROW_HEIGHT - 6 if self._items else self.EMPTY_HEIGHT)
        if self._auto_description:
            QWidget.setAccessibleDescription(self, self.default_description())
        self.updateGeometry()
        self.update()

    def set_data(self, items: Sequence[HBarItem]) -> None:
        """Alias of :meth:`set_items`."""
        self.set_items(items)

    def items(self) -> list[HBarItem]:
        """The rows."""
        return list(self._items)

    def set_empty_text(self, text: str) -> None:
        """Text shown when there are no rows."""
        self._empty_text = text
        if not self._items and self._auto_description:
            QWidget.setAccessibleDescription(self, self.default_description())
        self.update()

    def empty_text(self) -> str:
        """Text shown when there are no rows."""
        return self._empty_text

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        """Use *text* as the summary (stops the automatic one)."""
        self._auto_description = not text
        super().setAccessibleDescription(text or self.default_description())

    def default_description(self) -> str:
        """Plain-text summary of the rows."""
        if not self._items:
            return self._empty_text
        return "; ".join(f"{it.label}: {it.value_text or it.value}" for it in self._items) + "."

    def row_rect(self, index: int) -> QRectF:
        """Rect of row *index*."""
        return QRectF(0.0, float(index * self.ROW_HEIGHT), float(self.width()), float(self.ROW_HEIGHT - 6))

    def bar_rect(self, index: int) -> QRectF:
        """Filled part of row *index*'s bar."""
        if not 0 <= index < len(self._items):
            return QRectF()
        peak = max((it.value for it in self._items), default=0.0)
        frac = (self._items[index].value / peak) if peak > 0 else 0.0
        top = index * self.ROW_HEIGHT + self.BAR_TOP
        full = float(self.width())
        w = max(self.BAR_HEIGHT, full * frac) if frac > 0 else 0.0
        return QRectF(0.0, top, w, self.BAR_HEIGHT)

    def index_at(self, pos: QPointF) -> int | None:
        """Row under *pos*."""
        if not self._items:
            return None
        i = int(QPointF(pos).y() // self.ROW_HEIGHT)
        return i if 0 <= i < len(self._items) and 0 <= pos.x() <= self.width() else None

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            i = self.index_at(QPointF(e.pos()))
            if i is not None and self._items[i].tooltip:
                QToolTip.showText(e.globalPos(), self._items[i].tooltip, self, self.row_rect(i).toRect())
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        i = self.index_at(event.position())
        new = -1 if i is None else i
        if new != self._hover:
            self._hover = new
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(280, self.height())

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._items:
            p.setFont(fonts.font(13, 400))
            p.setPen(QColor(pal.text_muted))
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap
            p.drawText(QRectF(self.rect()), int(flags), self._empty_text)
            p.end()
            return
        label_font = fonts.font(13, 500)
        value_font = fonts.font(12, 500, tabular=True)
        lfm = QFontMetricsF(label_font)
        vfm = QFontMetricsF(value_font)
        width = float(self.width())
        for i, item in enumerate(self._items):
            top = i * self.ROW_HEIGHT
            hovered = i == self._hover
            value_w = vfm.horizontalAdvance(item.value_text) + 2.0
            p.setFont(value_font)
            p.setPen(QColor(pal.text if hovered else pal.text_muted))
            p.drawText(QRectF(width - value_w, top, value_w, 20.0),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), item.value_text)
            p.setFont(label_font)
            p.setPen(QColor(pal.text))
            avail = max(0.0, width - value_w - 12.0)
            p.drawText(QRectF(0.0, top, avail, 20.0), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       lfm.elidedText(item.label, Qt.TextElideMode.ElideRight, avail))
            track = QRectF(0.0, top + self.BAR_TOP, width, self.BAR_HEIGHT)
            fill_rounded(p, track, self.BAR_HEIGHT / 2.0, pal.surface_alt if pal.is_dark else pal.track)
            bar = self.bar_rect(i)
            if bar.width() > 0:
                color = QColor(item.color) if item.color is not None else QColor(pal.accent)
                if not hovered and self._hover >= 0:
                    color.setAlphaF(color.alphaF() * 0.7)
                fill_rounded(p, bar, self.BAR_HEIGHT / 2.0, color)
        p.end()
