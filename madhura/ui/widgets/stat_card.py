"""StatCard: one KPI tile on the Insights page (spec §1.9).

Layout (108 px tall, padding 16/20)::

    FOCUS TIME                     Streak 5 · best 12     ← micro-caps label · muted hint
    11h 20m                                               ← value 28/600, tabular
    ▲ 12%  vs previous 7 days                             ← delta (success up / muted down / —)

Numbers count up over 400 ms when set with :meth:`set_number` (instant with reduce-motion).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from madhura.ui import animations, fonts, theme
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.labels import ElidedLabel, Micro

__all__ = ["StatCard", "delta_text", "COUNT_UP_MS"]

#: Duration of the count-up animation.
COUNT_UP_MS = 400

ARROW_UP = "▲"
ARROW_DOWN = "▼"
NO_DELTA = "—"


def delta_text(delta: float | None, unit: str = "%") -> tuple[str, str]:
    """``(text, tone)`` for a delta.

    ``unit="%"``: *delta* is relative (0.12 → "▲ 12%"); ``unit="pts"``: *delta* is a difference
    of two rates (0.04 → "▲ 4 pts"). Up is "success", down is "muted" (never danger), no change
    reads "0%" / "0 pts" and None (nothing to compare with) reads "—".
    """
    if delta is None:
        return NO_DELTA, "muted"
    pct = int(round(abs(delta) * 100))
    suffix = "%" if unit == "%" else " pts" if pct != 1 else " pt"
    if pct == 0:
        return f"0{suffix}", "muted"
    if delta > 0:
        return f"{ARROW_UP} {pct:,}{suffix}", "success"
    return f"{ARROW_DOWN} {pct:,}{suffix}", "muted"


class StatCard(Card):
    """KPI tile: micro-caps ``label``, a 28/600 tabular value, a delta row and an optional hint."""

    def __init__(self, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(padding=(20, 16, 20, 16), parent=parent)
        self.setFixedHeight(108)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._label_text = label
        self._number: float | None = None
        self._fmt: Callable[[float], str] = lambda v: f"{int(round(v)):,}"
        self._delta: float | None = None
        self._delta_unit = "%"

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self._label = Micro(label, self)
        self._hint = ElidedLabel("", "caption", self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._hint.setProperty("tone", "muted")
        top.addWidget(self._label, 0)
        top.addWidget(self._hint, 1)

        self._value = QLabel("—", self)
        self._value.setFont(fonts.font(28, 600, tabular=True))
        self._value.setProperty("role", "value")
        self._value.setTextFormat(Qt.TextFormat.PlainText)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)
        self._delta_label = QLabel(NO_DELTA, self)
        self._delta_label.setFont(fonts.font(12, 600, tabular=True))
        self._delta_label.setProperty("tone", "muted")
        self._caption = ElidedLabel("", "caption", self)
        bottom.addWidget(self._delta_label, 0)
        bottom.addWidget(self._caption, 1)

        self.body.setSpacing(0)
        self.body.addLayout(top)
        self.body.addSpacing(6)
        self.body.addWidget(self._value)
        self.body.addSpacing(2)
        self.body.addLayout(bottom)
        self.body.addStretch(1)
        self._sync_accessible()

    # ------------------------------------------------------------------ value
    def set_value(self, text: str) -> None:
        """Show a literal value (stops any count-up)."""
        animations.stop_animations_key(self, "tween:value")
        self._number = None
        self._value.setText(text)
        self._sync_accessible()

    def set_number(
        self,
        value: float | None,
        fmt: Callable[[float], str] | None = None,
        *,
        animate: bool = True,
        start: float | None = None,
    ) -> None:
        """Show *value* formatted with *fmt*, counting up from the previous number (or *start*,
        default 0) over 400 ms. ``None`` shows "—"."""
        if fmt is not None:
            self._fmt = fmt
        if value is None:
            self.set_value("—")
            return
        target = float(value)
        begin = start if start is not None else (self._number if self._number is not None else 0.0)
        self._number = target
        fmt_fn = self._fmt
        self._sync_accessible()
        if not animate or begin == target:
            animations.stop_animations_key(self, "tween:value")
            self._value.setText(fmt_fn(target))
            return
        animations.tween(begin, target, COUNT_UP_MS, lambda v: self._value.setText(fmt_fn(v)), parent=self, key="value",
                         on_finished=lambda: self._value.setText(fmt_fn(target)))

    def number(self) -> float | None:
        """The (final) numeric value, None for a literal / empty value."""
        return self._number

    def value_text(self) -> str:
        """Final value text (what the count-up ends on)."""
        if self._number is not None:
            return self._fmt(self._number)
        return self._value.text()

    def displayed_text(self) -> str:
        """Value text currently on screen (mid-animation it differs from :meth:`value_text`)."""
        return self._value.text()

    # ------------------------------------------------------------------ delta / hint
    def set_delta(self, delta: float | None, caption: str = "", *, unit: str = "%") -> None:
        """Delta row: ``delta`` (see :func:`delta_text`) + caption ("vs previous 7 days")."""
        self._delta = delta
        self._delta_unit = unit
        text, tone = delta_text(delta, unit)
        self._delta_label.setText(text)
        theme.set_prop(self._delta_label, "tone", tone)
        self._caption.set_text(caption)
        self._sync_accessible()

    def delta_text(self) -> str:
        """Text of the delta ("▲ 12%")."""
        return self._delta_label.text()

    def delta_tone(self) -> str:
        """Tone of the delta ("success" / "muted")."""
        return str(self._delta_label.property("tone") or "")

    def caption_text(self) -> str:
        """The comparison caption."""
        return self._caption.full_text()

    def set_hint(self, text: str | None) -> None:
        """Muted hint at the top right ("Streak 5 · best 12"); empty hides it."""
        self._hint.set_text(text or "")
        self._sync_accessible()

    def hint_text(self) -> str:
        """The hint."""
        return self._hint.full_text()

    def label_text(self) -> str:
        """The KPI name."""
        return self._label_text

    def _sync_accessible(self) -> None:
        parts = [self.value_text()]
        delta = self._delta_label.text() if hasattr(self, "_delta_label") else ""
        if delta and delta != NO_DELTA:
            parts.append(f"{delta.replace(ARROW_UP, 'up').replace(ARROW_DOWN, 'down')} {self._caption.full_text()}".strip())
        hint = self._hint.full_text()
        if hint:
            parts.append(hint)
        self.setAccessibleName(self._label_text)
        self.setAccessibleDescription(", ".join(p for p in parts if p))
