"""Private building blocks of the habits UI [H]: emoji glyph, the habit check circle, the last-7-days
dot strip, the streak chip and the today segment bar, plus small text helpers.

Everything paints from ``theme.current().palette`` at paint time, so theme and accent switches
need no rebuild.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTextBoundaryFinder
from PySide6.QtGui import QColor, QFont, QFontMetrics, QHelpEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from madhura.core.timefmt import fmt_date_short, fmt_time
from madhura.ui import fonts, icons, theme
from madhura.ui.widgets.checkbox import Checkbox

__all__ = [
    "EMOJI_FAMILIES",
    "CURATED_EMOJI",
    "emoji_font",
    "first_grapheme",
    "fmt_hhmm",
    "STATE_TEXT",
    "HabitGlyph",
    "CheckCircle",
    "DotStrip",
    "StreakChip",
    "SegmentBar",
]

#: Colour-emoji families in preference order (Qt falls back further on its own).
EMOJI_FAMILIES = ["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", "Twemoji Mozilla", "EmojiOne Color"]

#: The 24 curated emoji offered by the editor's emoji popover.
CURATED_EMOJI: tuple[str, ...] = (
    "🎯", "📖", "🧘", "🏃", "💪", "🚶",
    "💧", "🥗", "🍎", "😴", "💊", "🧠",
    "💡", "✍️", "📝", "💻", "🚀", "🎸",
    "🎨", "🌱", "🧹", "📞", "🙏", "☀️",
)  # fmt: skip

#: Tooltip / accessible wording for day states.
STATE_TEXT = {
    "done": "Done",
    "skipped": "Skipped",
    "missed": "Missed",
    "pending": "Not yet",
    "unscheduled": "Not scheduled",
}


def emoji_font(px: int) -> QFont:
    """A font that renders colour emoji at *px* pixels."""
    f = QFont()
    f.setFamilies([*EMOJI_FAMILIES, fonts.family_for(px)])
    f.setPixelSize(max(1, int(px)))
    return f


def first_grapheme(text: str) -> str:
    """The first user-perceived character (grapheme cluster) of *text*, or "" (whitespace → "")."""
    text = (text or "").strip()
    if not text:
        return ""
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    finder.toStart()
    end = finder.toNextBoundary()
    if end <= 0:
        return text
    # QTextBoundaryFinder positions are UTF-16 code units.
    raw = text.encode("utf-16-le")
    return raw[: end * 2].decode("utf-16-le", errors="ignore")


def fmt_hhmm(value: str | None, use_24h: bool) -> str:
    """``"08:00"`` → ``"08:00"`` or ``"8:00 AM"`` (invalid/None → "")."""
    if not value:
        return ""
    try:
        hh, mm = value.split(":", 1)
        t = time(int(hh), int(mm[:2]))
    except (ValueError, TypeError):
        return value
    return fmt_time(datetime.combine(date(2000, 1, 1), t), use_24h)


# =========================================================================== glyph


class HabitGlyph(QWidget):
    """The habit's emoji centred in a ``size`` box; without an emoji, its initial in a soft circle."""

    def __init__(
        self,
        emoji: str | None = None,
        name: str = "",
        size: int = 28,
        parent: QWidget | None = None,
        *,
        emoji_px: int = 20,
    ) -> None:
        super().__init__(parent)
        self._emoji = emoji or ""
        self._name = name or ""
        self._emoji_px = emoji_px
        self._muted = False
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_habit(self, emoji: str | None, name: str) -> None:
        """Update the emoji / fallback initial."""
        emoji = emoji or ""
        if emoji != self._emoji or name != self._name:
            self._emoji = emoji
            self._name = name or ""
            self.update()

    def emoji(self) -> str:
        """The emoji shown ("" when the initial is shown)."""
        return self._emoji

    def set_muted(self, muted: bool) -> None:
        """Dim the glyph (done / not-today rows)."""
        if muted != self._muted:
            self._muted = muted
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if self._muted:
            p.setOpacity(0.6)
        r = QRectF(self.rect())
        if self._emoji:
            p.setFont(emoji_font(self._emoji_px))
            p.setPen(QColor(pal.text))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self._emoji)
        else:
            d = min(r.width(), r.height()) - 4
            circle = QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.accent_soft))
            p.drawEllipse(circle)
            initial = (self._name.strip()[:1] or "•").upper()
            p.setFont(fonts.font(max(10, int(d * 0.46)), 600))
            p.setPen(QColor(pal.accent))
            p.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), initial)
        p.end()


# =========================================================================== check circle


class CheckCircle(Checkbox):
    """28 px round habit check inside a 36 px hit area.

    Checked = done (accent fill + tick, 1 → 1.1 → 1 pulse). :meth:`set_skipped` shows a dashed
    outline with a skip glyph. Takes no keyboard focus (rows handle the keyboard).
    """

    HIT = 36

    def __init__(self, parent: QWidget | None = None, *, accessible_name: str = "Done") -> None:
        super().__init__(True, "", parent, box=28, accessible_name=accessible_name)
        self._skipped = False
        self.setFixedSize(self.HIT, self.HIT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _glyph_rect(self) -> QRectF:  # centred instead of left-aligned
        b = self._box
        return QRectF((self.width() - b) / 2.0, (self.height() - b) / 2.0, b, b)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.HIT, self.HIT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def is_skipped(self) -> bool:
        """True while the skipped look is shown."""
        return self._skipped

    def set_skipped(self, skipped: bool) -> None:
        """Dashed outline + skip glyph (only while unchecked)."""
        self._skipped = bool(skipped)
        self.set_dashed(self._skipped)
        self.update()

    def set_state(self, state: str) -> None:
        """Show "done" / "skipped" / "none" (``clicked`` is not emitted; ``toggled`` is)."""
        done = state == "done"
        if self.isChecked() != done:
            self.setChecked(done)
        self.set_skipped(state == "skipped")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._skipped and not self.isChecked():
            pal = theme.palette()
            p = QPainter(self)
            g = 14
            pm = icons.pixmap("skip", g, pal.text_muted, self.devicePixelRatioF())
            p.drawPixmap(int((self.width() - g) / 2), int((self.height() - g) / 2), pm)
            p.end()


# =========================================================================== last 7 days


class DotStrip(QWidget):
    """Seven 8 px dots (oldest → today, 6 px gap) with a weekday + status tooltip per dot.

    done = accent filled · skipped = hollow ring · missed = dim filled · pending = ring (today) ·
    unscheduled = tiny dot.
    """

    DOT = 8
    GAP = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: list[tuple[date, str]] = []
        w = 7 * self.DOT + 6 * self.GAP
        self.setFixedSize(w, 24)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Last 7 days")

    def days(self) -> list[tuple[date, str]]:
        """The ``(date, state)`` pairs shown."""
        return list(self._days)

    def set_days(self, days: Sequence[tuple[date, str]]) -> None:
        """Set the 7 ``(date, state)`` pairs (oldest first)."""
        days = list(days)[-7:]
        if days == self._days:
            return
        self._days = days
        counts = {s: sum(1 for _d, st in days if st == s) for s in ("done", "skipped", "missed")}
        self.setAccessibleDescription(
            f"Last 7 days: {counts['done']} done, {counts['skipped']} skipped, {counts['missed']} missed"
        )
        self.update()

    def _dot_rect(self, i: int) -> QRectF:
        x = i * (self.DOT + self.GAP)
        return QRectF(x, (self.height() - self.DOT) / 2.0, self.DOT, self.DOT)

    def tooltip_for(self, i: int) -> str:
        """Tooltip text of dot *i* ("Tue 13 Oct · Done")."""
        if not 0 <= i < len(self._days):
            return ""
        d, state = self._days[i]
        return f"{fmt_date_short(d)} · {STATE_TEXT.get(state, state)}"

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            for i in range(len(self._days)):
                if self._dot_rect(i).adjusted(-3, -8, 3, 8).contains(e.pos()):
                    QToolTip.showText(e.globalPos(), self.tooltip_for(i), self, self._dot_rect(i).toRect())
                    return True
            QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i, (_d, state) in enumerate(self._days):
            r = self._dot_rect(i)
            if state == "done":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.accent))
                p.drawEllipse(r)
            elif state in ("skipped", "pending"):
                color = pal.text_subtle if state == "skipped" else pal.border_strong
                p.setPen(QPen(QColor(color), 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(r.adjusted(0.7, 0.7, -0.7, -0.7))
            elif state == "missed":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.track))
                p.drawEllipse(r)
            else:  # unscheduled
                c = r.center()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.border_strong))
                p.drawEllipse(QRectF(c.x() - 1.5, c.y() - 1.5, 3, 3))
        p.end()


# =========================================================================== streak chip


class StreakChip(QWidget):
    """Flame + current streak in a warm pill, right-aligned in a fixed slot (empty at 0 so the dot
    columns of all rows stay aligned)."""

    SLOT = 50

    def __init__(self, parent: QWidget | None = None, *, slot: int = SLOT) -> None:
        super().__init__(parent)
        self._count = 0
        self._best = 0
        self.setFont(fonts.font(12, 600, tabular=True))
        self.setFixedSize(slot, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def count(self) -> int:
        """Current streak shown (0 = hidden)."""
        return self._count

    def set_streak(self, current: int, best: int = 0) -> None:
        """Set ``(current, best)``."""
        self._count = max(0, int(current))
        self._best = max(int(best), self._count)
        if self._count:
            self.setToolTip(f"{self._count}-day streak · best {self._best}")
            self.setAccessibleName(f"{self._count}-day streak")
        else:
            self.setToolTip("")
            self.setAccessibleName("")
        self.update()

    def is_shown(self) -> bool:
        """True when the chip is painted (streak > 0)."""
        return self._count > 0

    def pill_rect(self) -> QRectF:
        """The painted pill (right-aligned in the slot)."""
        text_w = QFontMetrics(self.font()).horizontalAdvance(str(self._count))
        w = min(self.width(), 6 + 12 + 3 + text_w + 8)
        return QRectF(self.width() - w, 0, w, self.height())

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        if not self._count:
            return
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.pill_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.warning_soft))
        p.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        pm = icons.pixmap("flame", 12, pal.warning, self.devicePixelRatioF())
        p.drawPixmap(int(r.left() + 6), int((r.height() - 12) / 2), pm)
        p.setPen(QColor(pal.warning))
        p.setFont(self.font())
        p.drawText(QRectF(r.left() + 21, 0, r.width() - 21, r.height()), int(Qt.AlignmentFlag.AlignVCenter), str(self._count))
        p.end()


# =========================================================================== segment bar


class SegmentBar(QWidget):
    """One rounded segment per scheduled habit (3 px gaps): done = accent, skipped = hollow track
    outline, otherwise track. Fixed 240×8 by default."""

    def __init__(self, parent: QWidget | None = None, *, width: int = 240, height: int = 8, gap: int = 3) -> None:
        super().__init__(parent)
        self._states: list[str] = []
        self._gap = gap
        self.setFixedSize(width, height)
        self.setAccessibleName("Today's habits")

    def states(self) -> list[str]:
        """Per-segment states ("done" / "skipped" / "none")."""
        return list(self._states)

    def set_states(self, states: Sequence[str]) -> None:
        """Set the segment states (one per scheduled habit, display order)."""
        states = list(states)
        if states != self._states:
            self._states = states
            done = sum(1 for s in states if s == "done")
            self.setAccessibleDescription(f"{done} of {len(states)} done")
            self.update()

    def segment_rects(self) -> list[QRectF]:
        """Rectangles of the segments."""
        n = len(self._states)
        if n == 0:
            return []
        w = (self.width() - self._gap * (n - 1)) / n
        return [QRectF(i * (w + self._gap), 0, w, self.height()) for i in range(n)]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rects = self.segment_rects()
        if not rects:
            r = QRectF(self.rect())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.track))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        for state, r in zip(self._states, rects, strict=False):
            radius = min(r.height(), r.width()) / 2.0
            if state == "done":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.accent))
                p.drawRoundedRect(r, radius, radius)
            elif state == "skipped":
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(pal.track if pal.is_dark else pal.border_strong), 1.2))
                inner = r.adjusted(0.6, 0.6, -0.6, -0.6)
                p.drawRoundedRect(inner, radius - 0.6, radius - 0.6)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.track))
                p.drawRoundedRect(r, radius, radius)
        p.end()

