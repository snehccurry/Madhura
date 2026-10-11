"""Shared pieces for the Settings page and the onboarding sheet ([S]).

Copy (nudge-mode explanations, labels), small formatters, the keyboard-accessible
:class:`AccentPicker` and the data actions both screens use (backup + legacy habit import).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFocusEvent, QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from madhura.core.timefmt import MONTHS_SHORT, fmt_date_short, fmt_duration, fmt_time
from madhura.ui import icons, theme
from madhura.ui.widgets._common import KeyboardFocusMixin

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = [
    "NUDGE_EXPLANATIONS",
    "NUDGE_CHOICES",
    "ACCENT_LABELS",
    "BackupFailed",
    "minutes_label",
    "goal_label",
    "hour_label",
    "time_example",
    "work_hours_hint",
    "shorten_path",
    "backup_label",
    "take_backup",
    "import_legacy",
    "open_path",
    "AccentPicker",
]

log = logging.getLogger(__name__)

_GENTLE_NUDGE = "A quiet panel in the corner asks what's next. It never takes focus."

#: One-line explanation of each nudge mode (Settings → Accountability).
NUDGE_EXPLANATIONS: dict[str, str] = {
    "off": "No nudges. Madhura stays quiet between blocks.",
    "gentle": _GENTLE_NUDGE,
    "firm": "Brings Madhura forward between blocks — without stealing focus — until you plan or snooze.",
}

#: (mode, title, body, icon) for the onboarding accountability cards.
NUDGE_CHOICES: tuple[tuple[str, str, str, str], ...] = (
    ("gentle", "Gentle", _GENTLE_NUDGE, "bell"),
    ("firm", "Firm", "Madhura comes forward until you plan the next block or snooze.", "flag"),
    ("off", "Off", "No nudges. You plan when you're ready.", "bell_off"),
)

ACCENT_LABELS: dict[str, str] = {
    "indigo": "Indigo",
    "teal": "Teal",
    "rose": "Rose",
    "amber": "Amber",
    "violet": "Violet",
}


class BackupFailed(RuntimeError):
    """A safety backup could not be written, so the destructive action was not attempted."""


# =========================================================================== formatters


def minutes_label(minutes: int) -> str:
    """Select label for a duration: ``"25 min"``, ``"1 hour"``, ``"2 hours"``, ``"90 min"``."""
    minutes = int(minutes)
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return "1 hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} min"


def goal_label(hours: float) -> str:
    """Daily-goal stepper text: ``"30m"``, ``"2h"``, ``"2h 30m"``."""
    return fmt_duration(float(hours) * 3600)


def hour_label(hour: int, use_24h: bool) -> str:
    """``"Midnight"`` for 0, else the clock time of *hour* (``"04:00"`` / ``"4:00 AM"``)."""
    if int(hour) == 0:
        return "Midnight"
    return fmt_time(datetime(2000, 1, 1, int(hour), 0), use_24h)


def time_example(use_24h: bool) -> str:
    """Description of the time format: ``"Times look like 14:35."``."""
    return f"Times look like {fmt_time(datetime(2000, 1, 1, 14, 35), use_24h)}."


def work_hours_hint(start: str, end: str, *, short: bool = False) -> str:
    """Explains the work-hours window (all day when equal, overnight when start > end).

    ``short`` gives the compact form shown next to the fields in onboarding.
    """
    if start == end:
        return "All day" if short else "Same start and end — nudges can happen all day."
    if start > end:
        return "Overnight" if short else "Overnight — the window ends the next morning."
    return "Nudges happen only in this window." if short else "Nudges only happen inside these hours."


def shorten_path(path: str | os.PathLike[str], max_chars: int = 56) -> str:
    """Display form of a path: ``~`` for the home folder (not on Windows), middle-elided."""
    text = str(path)
    if sys.platform != "win32":
        home = str(Path.home())
        if text == home or text.startswith(home + os.sep):
            text = "~" + text[len(home):]
    if len(text) <= max_chars:
        return text
    keep = max_chars - 1
    head = keep // 3
    return text[:head] + "…" + text[len(text) - (keep - head):]


_STAMP_RE = re.compile(r"^madhura-(?:(pre-[a-z]+)-)?(\d{8})-(\d{6})$")
_DAILY_RE = re.compile(r"^madhura-(\d{4})-(\d{2})-(\d{2})$")
_KIND_LABELS = {
    "pre-import": "Before import",
    "pre-reset": "Before reset",
    "pre-restore": "Before restore",
}


def backup_label(path: str | os.PathLike[str]) -> str:
    """Friendly menu label of a backup file (``"Daily backup · Wed 14 Oct"``)."""
    p = Path(path)
    stem = p.stem
    daily = _DAILY_RE.match(stem)
    if daily:
        try:
            from datetime import date

            d = date(int(daily.group(1)), int(daily.group(2)), int(daily.group(3)))
            return f"Daily backup · {fmt_date_short(d)}"
        except ValueError:
            pass
    stamped = _STAMP_RE.match(stem)
    if stamped:
        kind, ymd, hms = stamped.groups()
        title = _KIND_LABELS.get(kind or "", "Backup")
        try:
            month = MONTHS_SHORT[int(ymd[4:6]) - 1]
            return f"{title} · {int(ymd[6:8])} {month}, {hms[:2]}:{hms[2:4]}"
        except (IndexError, ValueError):
            pass
    try:
        when = datetime.fromtimestamp(p.stat().st_mtime)
        return f"{stem} · {when.day} {MONTHS_SHORT[when.month - 1]}, {when.hour:02d}:{when.minute:02d}"
    except OSError:
        return stem


# =========================================================================== data actions


def take_backup(ctx: AppContext, tag: str) -> Path:
    """Write ``backups/madhura-<tag>-<YYYYmmdd-HHMMSS>.db``; raises :class:`BackupFailed`."""
    from madhura.data import backup

    stamp = ctx.clock.now().strftime("%Y%m%d-%H%M%S")
    try:
        return backup.backup_now(ctx.repo.conn, ctx.data_dir / "backups", f"{tag}-{stamp}")
    except (OSError, sqlite3.Error) as exc:
        log.exception("Safety backup %s failed", tag)
        raise BackupFailed(str(exc)) from exc


def import_legacy(ctx: AppContext, path: str | os.PathLike[str]) -> int:
    """Import an old ``habits.json`` safely: nothing new → 0; else backup, import, then
    ``data_changed('habits')``. Raises ``LegacyImportError`` (bad file), :class:`BackupFailed`
    or ``sqlite3.Error``."""
    from madhura.data import legacy_import

    if not legacy_import.pending_legacy_habits(path, ctx.repo):
        return 0
    take_backup(ctx, "pre-import")
    added = legacy_import.import_legacy_habits(path, ctx.repo)
    if added:
        ctx.events.data_changed.emit("habits")
    return added


def open_path(path: str | os.PathLike[str]) -> bool:
    """Open a folder/file with the OS (file manager). Returns False when that failed."""
    try:
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
    except (RuntimeError, OSError):
        log.exception("Could not open %s", path)
        return False


# =========================================================================== accent picker


class AccentPicker(KeyboardFocusMixin, QWidget):
    """Five round accent swatches (Indigo, Teal, Rose, Amber, Violet) behaving like a radio group.

    One tab stop; ←/→ (↑/↓) move *and* select, Home/End jump, Space/Enter select. Swatches use the
    accent variant of the current scheme and repaint on theme changes. ``valueChanged(str)`` fires
    on user changes only (``set_value`` is silent); :meth:`set_key_commit_delay` coalesces
    arrow-key runs into one emission (re-styling the app is expensive).
    """

    valueChanged = Signal(str)

    SWATCH = 22
    SLOT = 32
    GAP = 8

    def __init__(self, value: str = "indigo", parent: QWidget | None = None, *, names: Iterable[str] | None = None) -> None:
        super().__init__(parent)
        self._names: list[str] = list(names) if names is not None else list(theme.ACCENT_NAMES)
        self._value = value if value in self._names else self._names[0]
        self._focus = self._names.index(self._value)
        self._hover = -1
        self._press = -1
        self._ring_radius = self.SLOT / 2.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        n = len(self._names)
        self.setFixedSize(n * self.SLOT + (n - 1) * self.GAP, self.SLOT)
        self.setAccessibleName("Accent color")
        self._sync_accessible()
        self._reported = self._value  # what listeners last heard (emitted or set_value)
        self._key_commit_ms = 0
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._commit)
        theme.current().changed.connect(self.update)

    def set_key_commit_delay(self, ms: int) -> None:
        """Arrow/Home/End keys select at once but emit ``valueChanged`` only after *ms* without
        another key (Space/Enter, focus-out or hiding emit at once). Clicks always emit at once.
        0 (default) emits on every key."""
        self._key_commit_ms = max(0, int(ms))

    # ------------------------------------------------------------------ API
    def names(self) -> list[str]:
        """Accent names in display order."""
        return list(self._names)

    def value(self) -> str:
        """Selected accent name."""
        return self._value

    def set_value(self, name: str) -> None:
        """Select *name* without emitting (unknown names are ignored)."""
        if name in self._names:
            self._commit_timer.stop()
            self._value = name
            self._reported = name
            self._focus = self._names.index(name)
            self._sync_accessible()
            self.update()
            self.update_focus_ring()

    def focus_index(self) -> int:
        """Index of the swatch the keyboard focus is on."""
        return self._focus

    def swatch_rect(self, index: int) -> QRect:
        """Slot rectangle of swatch *index* (widget coordinates)."""
        return QRect(index * (self.SLOT + self.GAP), 0, self.SLOT, self.SLOT)

    def focus_ring_rect(self) -> QRect | None:
        return self.swatch_rect(self._focus)

    # ------------------------------------------------------------------ behaviour
    def _sync_accessible(self) -> None:
        self.setAccessibleDescription(f"{ACCENT_LABELS.get(self._value, self._value.title())} selected")

    def _choose(self, index: int, *, from_key: bool = False) -> None:
        if not 0 <= index < len(self._names):
            return
        self._focus = index
        self._value = self._names[index]
        self._sync_accessible()
        self.update()
        self.update_focus_ring()
        if from_key and self._key_commit_ms > 0:
            self._commit_timer.start(self._key_commit_ms)
        else:
            self._commit()

    def _commit(self) -> None:
        """Emit the selection now (once, and only if it really changed)."""
        self._commit_timer.stop()
        if self._value != self._reported:
            self._reported = self._value
            self.valueChanged.emit(self._value)

    def _index_at(self, pos: QPoint) -> int:
        for i in range(len(self._names)):
            if self.swatch_rect(i).adjusted(-self.GAP // 2, 0, self.GAP // 2, 0).contains(pos):
                return i
        return -1

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        n = len(self._names)
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._choose((self._focus - 1) % n, from_key=True)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._choose((self._focus + 1) % n, from_key=True)
        elif key == Qt.Key.Key_Home:
            self._choose(0, from_key=True)
        elif key == Qt.Key.Key_End:
            self._choose(n - 1, from_key=True)
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._choose(self._focus)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event: object) -> None:  # noqa: N802
        self._focus = self._names.index(self._value)
        super().focusInEvent(event)  # type: ignore[arg-type]

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        if self._commit_timer.isActive():
            self._commit()
        super().focusOutEvent(event)

    def hideEvent(self, event: object) -> None:  # noqa: N802
        if self._commit_timer.isActive():
            self._commit()
        super().hideEvent(event)  # type: ignore[arg-type]

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self._index_at(event.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = self._index_at(event.position().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            idx = self._index_at(event.position().toPoint())
            if idx >= 0 and idx == self._press:
                self._choose(idx)
        self._press = -1
        event.accept()

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            idx = self._index_at(e.pos())
            if idx >= 0:
                name = self._names[idx]
                QToolTip.showText(e.globalPos(), ACCENT_LABELS.get(name, name.title()), self, self.swatch_rect(idx))
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.size()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        variant = 0 if pal.is_dark else 1
        for i, name in enumerate(self._names):
            slot = QRectF(self.swatch_rect(i))
            c = QColor(theme.ACCENTS[name][variant])
            center = slot.center()
            selected = name == self._value
            # While the keyboard focus ring circles the selected swatch it doubles as the selection ring.
            ring_drawn_by_focus = selected and self._kbd_focus and self.hasFocus() and i == self._focus
            if (selected and not ring_drawn_by_focus) or (i == self._hover and not selected):
                ring = QColor(c) if selected else QColor(pal.border_strong)
                r = self.SLOT / 2.0 - 1.0
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(ring, 2.0))
                p.drawEllipse(center, r, r)
            r = self.SWATCH / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(center, r, r)
            if selected:
                g = 14
                pm = icons.pixmap("check", g, pal.accent_text, self.devicePixelRatioF())
                p.drawPixmap(int(round(center.x() - g / 2)), int(round(center.y() - g / 2)), pm)
        p.end()
