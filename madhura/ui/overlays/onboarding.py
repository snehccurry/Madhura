"""First-run OnboardingSheet (spec §1.11).

Four steps in one in-window sheet that ignores Esc, with step dots and Back / Next:

1. **Welcome** — logo, "Plan the hour. Stay with it." and three benefit rows.
2. **Your day** — daily goal chips (1h–4h, default 2h), work days, work hours and "Start Madhura
   when I sign in" (default on where launch at login is supported).
3. **Accountability** — three selectable cards (Gentle / Firm / Off); when an old Madhura
   ``habits.json`` is found, an "Import N habits from old Madhura" toggle (on).
4. **First block** — task field + duration chips (25m preselected) + inline "Lock in", or
   "Skip for now".

Nothing is written until the user finishes: then the settings are applied together with
``onboarded=True``, launch at login and the legacy import happen (backup first), and "Lock in"
starts the first session via ``engine.start``. The shell (``app.after_show``) shows this sheet
while ``settings.onboarded`` is False.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from madhura.core.timefmt import fmt_preset, fmt_time
from madhura.platform import autostart
from madhura.ui import animations, fonts, icons, theme
from madhura.ui.pages._settings_kit import NUDGE_CHOICES, BackupFailed, import_legacy, shorten_path, work_hours_hint
from madhura.ui.widgets._common import KeyboardFocusMixin, fill_rounded
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.chips import ChipGroup, DayChips
from madhura.ui.widgets.empty_state import IconMedallion
from madhura.ui.widgets.inputs import TextField, TimeField
from madhura.ui.widgets.labels import Label, Micro
from madhura.ui.widgets.settings_row import Divider
from madhura.ui.widgets.sheet import SheetContent
from madhura.ui.widgets.toggle import Toggle

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["OnboardingSheet", "StepDots", "ChoiceCards", "GOAL_OPTIONS", "BENEFITS", "STEP_TITLES"]

log = logging.getLogger(__name__)

#: Daily goal chips (minutes, label).
GOAL_OPTIONS: tuple[tuple[int, str], ...] = ((60, "1h"), (120, "2h"), (180, "3h"), (240, "4h"))
#: Welcome-step benefit rows: (icon, title, caption).
BENEFITS: tuple[tuple[str, str, str], ...] = (
    ("target", "Plan one block at a time", "Decide what the next hour is for, then lock in."),
    ("check_circle", "Gentle check-ins keep you honest", "A quiet “still on it?” every few minutes. Only you see it."),
    ("repeat", "Habits that compound", "Improve by 1% every day — small wins add up."),
)
STEP_TITLES: tuple[str, ...] = ("Welcome", "Your day", "Accountability", "First block")
FIRST_BLOCK_MINUTES = 25
EMPTY_TASK_ERROR = "Name the one thing you'll work on"
_STEP_FADE_MS = 180


# =========================================================================== small widgets


class StepDots(QWidget):
    """Progress dots: the current step is a short accent pill, the others small track dots."""

    DOT = 6
    ACTIVE = 18
    GAP = 6

    def __init__(self, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = max(1, int(count))
        self._current = 0
        self.setFixedSize(self.ACTIVE + (self._count - 1) * (self.DOT + self.GAP), 12)
        self._sync_accessible()

    def current(self) -> int:
        """Index of the current step."""
        return self._current

    def set_current(self, index: int) -> None:
        """Highlight step *index*."""
        self._current = max(0, min(self._count - 1, int(index)))
        self._sync_accessible()
        self.update()

    def _sync_accessible(self) -> None:
        self.setAccessibleName(f"Step {self._current + 1} of {self._count}")

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = 0.0
        y = (self.height() - self.DOT) / 2.0
        for i in range(self._count):
            w = self.ACTIVE if i == self._current else self.DOT
            color = pal.accent if i == self._current else (pal.border_strong if i > self._current else pal.text_subtle)
            fill_rounded(p, QRectF(x, y, w, self.DOT), self.DOT / 2.0, color)
            x += w + self.GAP
        p.end()


class _ChoiceCard(QWidget):
    """One painted option card (icon, title, body, radio mark). Clicks go to the owning group."""

    def __init__(self, icon: str, title: str, body: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._icon = icon
        self.selected = False
        self.hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(60, 12, 48, 12)
        lay.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setFont(fonts.font(14, 600))
        self.body_label = Label(body, "caption", self)
        self.body_label.setWordWrap(True)
        lay.addWidget(self.title_label)
        lay.addWidget(self.body_label)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def enterEvent(self, event: object) -> None:  # noqa: N802
        self.hovered = True
        self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self.hovered = False
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        if self.selected:
            fill, border = QColor(pal.accent_soft), QColor(pal.accent_border)
        else:
            fill = theme.mix(pal.surface_alt, pal.hover, 0.7 if self.hovered else 0.0)
            border = QColor(pal.border_strong if self.hovered else pal.border)
        fill_rounded(p, r, 10.0, fill, border)
        # Icon tile.
        tile = QRectF(16, (r.height() - 32) / 2.0, 32, 32)
        tile_fill = pal.bg if (pal.is_dark and not self.selected) else pal.surface
        fill_rounded(p, tile, 8.0, tile_fill)
        color = pal.accent if self.selected else pal.text_muted
        pm = icons.pixmap(self._icon, 18, color, self.devicePixelRatioF())
        p.drawPixmap(int(tile.x() + 7), int(tile.y() + 7), pm)
        # Radio mark.
        c = QRectF(r.width() - 16 - 18, (r.height() - 18) / 2.0, 18, 18)
        if self.selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.accent))
            p.drawEllipse(c)
            p.setBrush(QColor(pal.accent_text))
            p.drawEllipse(c.center(), 3.5, 3.5)
        else:
            fill_rounded(p, c, 9.0, None, pal.text_subtle if self.hovered else pal.border_strong, 1.5)
        p.end()


class ChoiceCards(KeyboardFocusMixin, QWidget):
    """Vertical radio group of option cards: one tab stop, ↑/↓ move and select, click selects.

    ``valueChanged(object)`` fires on user changes only.
    """

    valueChanged = Signal(object)

    def __init__(self, options: Sequence[tuple[Any, str, str, str]], value: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[Any] = [o[0] for o in options]
        self._cards: list[_ChoiceCard] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for _value, title, body, icon in options:
            card = _ChoiceCard(icon, title, body, self)
            card.installEventFilter(self)
            lay.addWidget(card)
            self._cards.append(card)
        self._index = self._values.index(value) if value in self._values else 0
        self._press = -1
        self._ring_radius = 10.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._sync()

    def value(self) -> Any:
        """Selected value."""
        return self._values[self._index]

    def set_value(self, value: Any) -> None:
        """Select *value* without emitting."""
        if value in self._values:
            self._index = self._values.index(value)
            self._sync()

    def cards(self) -> list[QWidget]:
        """The card widgets (in order)."""
        return list(self._cards)

    def focus_ring_rect(self) -> QRect | None:
        return self._cards[self._index].geometry() if self._cards else None

    def _sync(self) -> None:
        for i, card in enumerate(self._cards):
            card.selected = i == self._index
            card.update()
        card = self._cards[self._index]
        self.setAccessibleName("Nudge style")
        self.setAccessibleDescription(f"{card.title_label.text()}: {card.body_label.text()}")
        self.update_focus_ring()

    def _choose(self, index: int) -> None:
        if not 0 <= index < len(self._cards):
            return
        changed = index != self._index
        self._index = index
        self._sync()
        if changed:
            self.valueChanged.emit(self.value())

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        if obj in self._cards and isinstance(event, QMouseEvent) and event.button() == Qt.MouseButton.LeftButton:
            idx = self._cards.index(obj)
            et = event.type()
            if et == QEvent.Type.MouseButtonPress:
                self._press = idx
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                return True
            if et == QEvent.Type.MouseButtonRelease:
                if self._press == idx and obj.rect().contains(event.position().toPoint()):
                    self._choose(idx)
                self._press = -1
                return True
        return False

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        n = len(self._cards)
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Left):
            self._choose((self._index - 1) % n)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Right):
            self._choose((self._index + 1) % n)
        elif key == Qt.Key.Key_Home:
            self._choose(0)
        elif key == Qt.Key.Key_End:
            self._choose(n - 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update_focus_ring()


# =========================================================================== helpers


def _title(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", "h1")
    label.setFont(fonts.role_font("h1"))
    label.setWordWrap(True)
    return label


def _lead(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", "muted")
    label.setFont(fonts.font(14))
    label.setWordWrap(True)
    return label


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(fonts.font(13, 600))
    return label


def _page(parent: QWidget) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget(parent)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    return page, lay


def _hline() -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    return row


class _BenefitRow(QWidget):
    def __init__(self, icon: str, title: str, caption: str, parent: QWidget) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.medallion = IconMedallion(icon, 36, self)
        lay.addWidget(self.medallion, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 1, 0, 0)
        col.setSpacing(2)
        t = QLabel(title, self)
        t.setFont(fonts.font(14, 600))
        col.addWidget(t)
        c = Label(caption, "caption", self)
        c.setWordWrap(True)
        col.addWidget(c)
        lay.addLayout(col, 1)


class _NextSteps(QWidget):
    """Soft panel on the first-block step: what happens after Lock in (follows the settings)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(Micro("What happens next", self))
        self.rows: list[tuple[IconMedallion, QLabel, QLabel]] = []
        for icon in ("eye_off", "check_circle"):
            row = QHBoxLayout()
            row.setSpacing(12)
            medallion = IconMedallion(icon, 28, self)
            row.addWidget(medallion, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(1)
            title = QLabel(self)
            title.setFont(fonts.font(13, 600))
            body = Label("", "caption", self)
            body.setWordWrap(True)
            col.addWidget(title)
            col.addWidget(body)
            row.addLayout(col, 1)
            lay.addLayout(row)
            self.rows.append((medallion, title, body))

    def refresh(self, ctx: AppContext) -> None:
        """Copy that matches ``after_start`` and the check-in settings."""
        (_m1, t1, b1), (m2, t2, b2) = self.rows
        if ctx.settings.get("after_start") == "mini":
            t1.setText("Madhura steps aside")
            b1.setText("The window tucks into a small floating timer so you can get to work.")
        else:
            t1.setText("The timer stays in view")
            b1.setText("Madhura keeps its window open while you work.")
        if ctx.settings.get("checkin_enabled"):
            minutes = int(ctx.settings.get("checkin_interval_minutes"))
            t2.setText("Quiet check-ins")
            b2.setText(f"About every {minutes} minutes: still on it? One click, then back to work.")
            m2.set_icon("check_circle")
        else:
            t2.setText("Check-ins are off")
            b2.setText("Turn them on any time in Settings to keep yourself honest.")
            m2.set_icon("bell_off")

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill_rounded(p, QRectF(self.rect()), 10.0, pal.surface_alt, pal.border)
        p.end()


# =========================================================================== sheet


class OnboardingSheet(SheetContent):
    """The four-step first-run sheet (ignores Esc). Finishing sets ``onboarded=True``.

    ``legacy_paths`` overrides old-habits detection (tests); by default
    ``data.legacy_import.find_legacy_habits()`` runs once at construction.
    """

    dismissible = False
    sheet_width = 520
    padding = 28

    def __init__(
        self,
        ctx: AppContext,
        parent: QWidget | None = None,
        *,
        legacy_paths: Sequence[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._step = 0
        self._done = False
        self.started_session_id: int | None = None
        self.imported_count = 0

        self.stack = QStackedWidget(self)
        self.body.addWidget(self.stack)
        self._build_welcome()
        self._build_day()
        self._build_accountability(legacy_paths)
        self._build_first_block()

        self.dots = StepDots(len(STEP_TITLES), self)
        self.footer_left.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignVCenter)
        self.back_button = Button("Back", "ghost", parent=self)
        self.back_button.clicked.connect(self.back)
        self.skip_button = Button("Skip for now", "ghost", parent=self)
        self.skip_button.clicked.connect(lambda: self.finish(start_block=False))
        self.next_button = Button("Get started", "primary", parent=self)
        self.next_button.setMinimumWidth(120)
        self.next_button.clicked.connect(self.next)
        self.add_footer_button(self.back_button)
        self.add_footer_button(self.skip_button)
        self.add_footer_button(self.next_button, primary=True)
        ctx.theme.changed.connect(self._repaint)
        self._apply_step(0, animate=False)

    # ------------------------------------------------------------------ pages
    def _build_welcome(self) -> None:
        page, lay = _page(self.stack)
        self.logo = IconMedallion("logo", 56, page)
        lay.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addSpacing(20)
        lay.addWidget(_title("Welcome to Madhura", page))
        lay.addSpacing(6)
        tagline = QLabel("Plan the hour. Stay with it.", page)
        tagline.setFont(fonts.font(16, 500))
        tagline.setProperty("role", "muted")
        lay.addWidget(tagline)
        lay.addSpacing(28)
        self.benefit_rows: list[_BenefitRow] = []
        for i, (icon, title, caption) in enumerate(BENEFITS):
            if i:
                lay.addSpacing(18)
            row = _BenefitRow(icon, title, caption, page)
            self.benefit_rows.append(row)
            lay.addWidget(row)
        lay.addStretch(1)
        self.stack.addWidget(page)

    def _build_day(self) -> None:
        ctx = self._ctx
        page, lay = _page(self.stack)
        lay.addWidget(_title("Shape your day", page))
        lay.addSpacing(6)
        lay.addWidget(
            _lead("Madhura uses this for your goal ring, streaks and nudges. You can change it any time in Settings.", page)
        )
        lay.addSpacing(24)

        lay.addWidget(_field_label("Daily focus goal", page))
        lay.addSpacing(8)
        current_goal = int(ctx.settings.get("daily_goal_minutes"))
        values = [v for v, _l in GOAL_OPTIONS]
        self.goal_chips = ChipGroup(GOAL_OPTIONS, True, current_goal if current_goal in values else 120, page, wrap=False)
        self.goal_chips.setAccessibleName("Daily focus goal")
        lay.addWidget(self.goal_chips, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addSpacing(20)

        lay.addWidget(_field_label("Work days", page))
        lay.addSpacing(8)
        self.work_days = DayChips(int(ctx.settings.get("work_days")), page)
        self.work_days.setAccessibleName("Work days")
        lay.addWidget(self.work_days, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addSpacing(20)

        lay.addWidget(_field_label("Work hours", page))
        lay.addSpacing(8)
        hours = _hline()
        hours.setSpacing(8)
        use_24h = ctx.use_24h()
        self.work_start = TimeField(ctx.settings.get("work_start"), page, use_24h=use_24h)
        self.work_end = TimeField(ctx.settings.get("work_end"), page, use_24h=use_24h)
        self.work_start.time_edit.setAccessibleName("Work hours start")
        self.work_end.time_edit.setAccessibleName("Work hours end")
        hours.addWidget(self.work_start)
        hours.addWidget(Label("to", "caption", page))
        hours.addWidget(self.work_end)
        hours.addSpacing(6)
        self.hours_hint = Label("", "caption", page)
        self.hours_hint.setWordWrap(True)
        hours.addWidget(self.hours_hint, 1)
        lay.addLayout(hours)
        self.work_start.timeChanged.connect(self._update_hours_hint)
        self.work_end.timeChanged.connect(self._update_hours_hint)
        self._update_hours_hint()

        self.autostart_supported = autostart.is_supported()
        self.autostart_toggle = Toggle(True, page, accessible_name="Start Madhura when I sign in")
        self.autostart_box = QWidget(page)
        box = QVBoxLayout(self.autostart_box)
        box.setContentsMargins(0, 24, 0, 0)
        box.setSpacing(16)
        box.addWidget(Divider(self.autostart_box))
        row = _hline()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_field_label("Start Madhura when I sign in", self.autostart_box))
        col.addWidget(Label("It opens quietly in the tray, ready for your first block.", "caption", self.autostart_box))
        row.addLayout(col, 1)
        self.autostart_toggle.setParent(self.autostart_box)
        row.addWidget(self.autostart_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(row)
        lay.addWidget(self.autostart_box)
        self.autostart_box.setVisible(self.autostart_supported)
        lay.addStretch(1)
        self.stack.addWidget(page)

    def _build_accountability(self, legacy_paths: Sequence[Path] | None) -> None:
        page, lay = _page(self.stack)
        lay.addWidget(_title("Stay intentional between blocks", page))
        lay.addSpacing(6)
        lay.addWidget(_lead("During work hours, Madhura can nudge you to plan your next block.", page))
        lay.addSpacing(24)
        current = self._ctx.settings.get("nudge_mode")
        self.nudge_cards = ChoiceCards(NUDGE_CHOICES, current, page)
        lay.addWidget(self.nudge_cards)

        self.legacy_path, self.legacy_count = self._detect_legacy(legacy_paths)
        self.legacy_toggle = Toggle(True, page)
        self.legacy_box = QWidget(page)
        box = QVBoxLayout(self.legacy_box)
        box.setContentsMargins(0, 24, 0, 0)
        box.setSpacing(16)
        box.addWidget(Divider(self.legacy_box))
        row = _hline()
        col = QVBoxLayout()
        col.setSpacing(2)
        n = self.legacy_count
        title = f"Import {n} habit{'s' if n != 1 else ''} from old Madhura"
        self.legacy_title = _field_label(title, self.legacy_box)
        col.addWidget(self.legacy_title)
        where = Label(shorten_path(self.legacy_path) if self.legacy_path else "", "caption", self.legacy_box)
        where.setToolTip(str(self.legacy_path or ""))
        col.addWidget(where)
        row.addLayout(col, 1)
        self.legacy_toggle.setParent(self.legacy_box)
        self.legacy_toggle.setAccessibleName(title)
        row.addWidget(self.legacy_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(row)
        lay.addWidget(self.legacy_box)
        self.legacy_box.setVisible(self.legacy_path is not None)
        lay.addStretch(1)
        self.stack.addWidget(page)

    def _detect_legacy(self, paths: Sequence[Path] | None) -> tuple[Path | None, int]:
        from madhura.data import legacy_import

        candidates = list(paths) if paths is not None else legacy_import.find_legacy_habits()
        for candidate in candidates:
            try:
                count = len(legacy_import.pending_legacy_habits(candidate, self._ctx.repo))
            except legacy_import.LegacyImportError:
                continue
            if count:
                return Path(candidate), count
        return None, 0

    def _build_first_block(self) -> None:
        ctx = self._ctx
        page, lay = _page(self.stack)
        lay.addWidget(_title("Plan your first block", page))
        lay.addSpacing(6)
        lay.addWidget(_lead("What's the one thing you'll focus on next? Keep it small and concrete.", page))
        lay.addSpacing(24)
        self.task_field = TextField("What will you focus on?", "target", 52, page)
        self.task_field.setAccessibleName("Task")
        self.task_field.returnPressed.connect(self.lock_in)
        self.task_field.textEdited.connect(lambda _t: self._relayout())
        lay.addWidget(self.task_field)
        lay.addSpacing(14)
        row = _hline()
        presets = [int(m) for m in ctx.settings.get("duration_presets")]
        default = FIRST_BLOCK_MINUTES if FIRST_BLOCK_MINUTES in presets else int(ctx.settings.get("default_minutes"))
        if default not in presets:
            default = presets[0]
        # Wrapping chips take the free width, so up to six presets never push Lock in out.
        self.duration_chips = ChipGroup([(m, fmt_preset(m)) for m in presets], True, default, page)
        self.duration_chips.setAccessibleName("Duration")
        self.duration_chips.selectionChanged.connect(lambda _v: self._update_preview())
        row.addWidget(self.duration_chips, 1, Qt.AlignmentFlag.AlignVCenter)
        self.lock_in_button = Button("Lock in", "primary", icon="play", size="lg", parent=page)
        self.lock_in_button.setMinimumWidth(140)
        self.lock_in_button.setToolTip("Lock in · Ctrl+Enter")
        self.lock_in_button.clicked.connect(self.lock_in)
        row.addWidget(self.lock_in_button, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row)
        lay.addSpacing(10)
        self.preview = Label("", "caption", page)
        self.preview.setFont(fonts.font(12, 400, tabular=True))
        lay.addWidget(self.preview)
        lay.addSpacing(24)
        self.next_steps = _NextSteps(page)
        lay.addWidget(self.next_steps)
        lay.addStretch(1)
        self.stack.addWidget(page)

    # ------------------------------------------------------------------ live bits
    def _update_hours_hint(self, *_args: object) -> None:
        self.hours_hint.setText(work_hours_hint(self.work_start.time_str(), self.work_end.time_str(), short=True))

    def _update_preview(self) -> None:
        ctx = self._ctx
        minutes = int(self.duration_chips.value() or FIRST_BLOCK_MINUTES)
        ends = ctx.clock.now() + timedelta(minutes=minutes)
        if ctx.settings.get("checkin_enabled"):
            checkins = f"check-ins every {int(ctx.settings.get('checkin_interval_minutes'))} min"
        else:
            checkins = "check-ins off"
        self.preview.setText(f"Ends at {fmt_time(ends, ctx.use_24h())} · {checkins}")
        self.next_steps.refresh(ctx)

    def _repaint(self) -> None:
        for w in (self.dots, self.logo, self.next_steps, *(r.medallion for r in self.benefit_rows)):
            w.update()

    def _relayout(self) -> None:
        """Re-fit the sheet card after the content height changed (e.g. the task error)."""
        host = self.host()
        fit = getattr(host, "relayout", None)
        if callable(fit):
            QTimer.singleShot(0, host, fit)  # host as context: dropped if it is deleted first

    # ------------------------------------------------------------------ navigation
    def step(self) -> int:
        """Current step index (0–3)."""
        return self._step

    def next(self) -> None:
        """Advance one step (the last step finishes via Lock in / Skip for now)."""
        if self._step < len(STEP_TITLES) - 1:
            self._apply_step(self._step + 1)

    def back(self) -> None:
        """Go back one step."""
        if self._step > 0:
            self._apply_step(self._step - 1)

    def _apply_step(self, index: int, animate: bool = True) -> None:
        self._step = index
        if animate:
            animations.cross_fade(self.stack, index, _STEP_FADE_MS)
        else:
            self.stack.setCurrentIndex(index)
        self.dots.set_current(index)
        last = index == len(STEP_TITLES) - 1
        self.back_button.setVisible(index > 0)
        self.skip_button.setVisible(last)
        self.next_button.setVisible(not last)
        self.next_button.setText("Get started" if index == 0 else "Next")
        self.set_primary(self.lock_in_button if last else self.next_button)
        if last:
            self._update_preview()
        self._focus_step()

    def _focus_step(self) -> None:
        if not self.isVisible():
            return
        target = self.initial_focus_widget()
        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def initial_focus_widget(self) -> QWidget | None:
        if self._step == 1:
            return self.goal_chips
        if self._step == 2:
            return self.nudge_cards
        if self._step == 3:
            return self.task_field.line_edit
        return self.next_button

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Enter activates a focused footer button, otherwise moves to the next step."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            focused = QApplication.focusWidget()
            if isinstance(focused, QPushButton) and self.isAncestorOf(focused) and focused.isEnabled():
                focused.click()
                event.accept()
                return
            if self._step < len(STEP_TITLES) - 1:
                self.next()
                event.accept()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ finishing
    def choices(self) -> dict[str, Any]:
        """Settings the sheet will apply on finish."""
        return {
            "daily_goal_minutes": int(self.goal_chips.value() or 120),
            "work_days": int(self.work_days.mask()),
            "work_start": self.work_start.time_str(),
            "work_end": self.work_end.time_str(),
            "nudge_mode": str(self.nudge_cards.value()),
        }

    def lock_in(self) -> None:
        """Start the first block (task required) and finish."""
        if not self.task_field.text().strip():
            self.task_field.set_error(EMPTY_TASK_ERROR)
            self.task_field.setFocus(Qt.FocusReason.OtherFocusReason)
            self._relayout()
            return
        self.finish(start_block=True)

    def finish(self, start_block: bool = False) -> None:
        """Apply every choice, mark onboarding done, optionally start the first block, close."""
        if self._done:
            return
        self._done = True
        ctx = self._ctx
        try:
            ctx.settings.update(**self.choices(), onboarded=True)
        except (KeyError, ValueError):
            log.exception("Could not apply the onboarding choices")
            ctx.settings.update(onboarded=True)
        problems: list[str] = []
        if self.autostart_supported:
            want = self.autostart_toggle.isChecked()
            if want != autostart.is_enabled() and not autostart.set_enabled(want):
                problems.append("Couldn't set up launch at login — you can try again in Settings.")
        if self.legacy_path is not None and self.legacy_toggle.isChecked():
            self.imported_count = self._import_legacy(problems)
        if start_block:
            task = " ".join(self.task_field.text().split())
            minutes = int(self.duration_chips.value() or FIRST_BLOCK_MINUTES)
            self.started_session_id = ctx.engine.start(task, minutes)
            if self.started_session_id is None and ctx.engine.is_active:
                problems.append("A session is already running.")
        self.close_sheet()
        window = ctx.window
        if window is None:
            return
        if problems:
            window.show_toast(problems[0], "warning")
        elif self.imported_count:
            n = self.imported_count
            window.show_toast(
                f"Imported {n} habit{'s' if n != 1 else ''} from old Madhura",
                "success",
                "View",
                lambda: ctx.events.navigate.emit("habits"),
            )

    def _import_legacy(self, problems: list[str]) -> int:
        from madhura.data.legacy_import import LegacyImportError

        assert self.legacy_path is not None
        try:
            return import_legacy(self._ctx, self.legacy_path)
        except (LegacyImportError, BackupFailed, sqlite3.Error):
            log.exception("Onboarding legacy import failed")
            problems.append("Couldn't import your old habits — try again from Settings › Data.")
            return 0
