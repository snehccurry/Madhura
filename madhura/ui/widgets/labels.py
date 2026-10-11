"""Typography and small label widgets: Heading, Caption, Micro, ElidedLabel, Badge, Kbd,
SectionHeader."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from madhura.ui import fonts, icons, theme
from madhura.ui.widgets._common import elide, fill_rounded

__all__ = [
    "BADGE_TONES",
    "Label",
    "Heading",
    "Caption",
    "Micro",
    "ElidedLabel",
    "Badge",
    "Kbd",
    "SectionHeader",
]

BADGE_TONES = ("neutral", "accent", "success", "warning", "danger", "break")


class Label(QLabel):
    """QLabel bound to a type-scale ``role`` (h1/h2/body/label/caption/micro) and optional
    colour ``tone`` (muted/subtle/accent/success/warning/danger/break)."""

    def __init__(self, text: str = "", role: str = "body", parent: QWidget | None = None, *, tone: str | None = None) -> None:
        super().__init__(parent)
        self._role = role
        self.setProperty("role", role)
        if tone:
            self.setProperty("tone", tone)
        self.setFont(fonts.role_font(role))
        self.setText(text)

    def role(self) -> str:
        """Type-scale role."""
        return self._role

    def set_role(self, role: str) -> None:
        """Switch role (updates font + QSS colour)."""
        self._role = role
        self.setFont(fonts.role_font(role))
        theme.set_prop(self, "role", role)

    def set_tone(self, tone: str | None) -> None:
        """Set/clear the colour tone."""
        theme.set_prop(self, "tone", tone or "")


class Heading(Label):
    """Page/section heading: ``role`` "h1" (24/600) or "h2" (16/600)."""

    def __init__(self, text: str = "", role: str = "h1", parent: QWidget | None = None) -> None:
        super().__init__(text, role if role in ("h1", "h2", "display", "display_sm") else "h1", parent)


class Caption(Label):
    """12 px muted caption."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, tone: str | None = None) -> None:
        super().__init__(text, "caption", parent, tone=tone)


class Micro(Label):
    """11/600 uppercase label with 0.6 px tracking (rendered uppercase automatically)."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, tone: str | None = None) -> None:
        super().__init__(text, "micro", parent, tone=tone)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText((text or "").upper())


class ElidedLabel(QLabel):
    """Single-line label that elides with "…" instead of growing; full text in the tooltip when
    elided. Expands horizontally but never forces its parent wider."""

    def __init__(self, text: str = "", role: str = "body", parent: QWidget | None = None, *, tone: str | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setProperty("role", role)
        if tone:
            self.setProperty("tone", tone)
        self.setFont(fonts.role_font(role))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.set_text(text)

    def full_text(self) -> str:
        """The un-elided text."""
        return self._full

    def text_full(self) -> str:  # alias
        """Alias of :meth:`full_text`."""
        return self._full

    def set_text(self, text: str) -> None:
        """Set the (un-elided) text."""
        self._full = text or ""
        self._refresh()

    def setText(self, text: str) -> None:  # noqa: N802
        self.set_text(text)

    def set_tone(self, tone: str | None) -> None:
        """Set/clear the colour tone."""
        theme.set_prop(self, "tone", tone or "")

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        return QSize(min(fm.horizontalAdvance(self._full) + 2, 480), fm.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, QFontMetrics(self.font()).height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._refresh()

    def _refresh(self) -> None:
        fm = QFontMetrics(self.font())
        shown = elide(self._full, fm, self.width()) if self.width() > 0 else self._full
        QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != self._full else "")


def _tone_colors(tone: str) -> tuple[QColor, QColor]:
    p = theme.palette()
    if tone == "accent":
        return QColor(p.accent_soft), QColor(p.accent)
    if tone == "success":
        return QColor(p.success_soft), QColor(p.success)
    if tone == "warning":
        return QColor(p.warning_soft), QColor(p.warning)
    if tone == "danger":
        return QColor(p.danger_soft), QColor(p.danger)
    if tone == "break":
        return QColor(p.break_soft), QColor(p.break_)
    return QColor(p.hover), QColor(p.text_muted)


class Badge(QWidget):
    """Small pill label (20 px). ``tone``: neutral | accent | success | warning | danger | break.

    Optional leading ``icon`` or colour ``dot``. With ``clickable=True`` it shows a hand cursor
    and emits :attr:`clicked` (e.g. the minutes badge that cycles presets).
    """

    clicked = Signal()

    def __init__(
        self,
        text: str = "",
        tone: str = "neutral",
        parent: QWidget | None = None,
        *,
        icon: str | None = None,
        dot: QColor | str | None = None,
        clickable: bool = False,
        tabular: bool = True,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._tone = tone if tone in BADGE_TONES else "neutral"
        self._icon = icon
        self._dot = QColor(dot) if dot is not None else None
        self._hover = False
        self.setFont(fonts.font(11, 600, tabular))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_clickable(clickable)
        self.setAccessibleName(text)

    def text(self) -> str:
        """Badge text."""
        return self._text

    def set_text(self, text: str) -> None:
        """Change the text."""
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802
        self.set_text(text)

    def tone(self) -> str:
        """Current tone."""
        return self._tone

    def set_tone(self, tone: str) -> None:
        """Change the tone."""
        self._tone = tone if tone in BADGE_TONES else "neutral"
        self.update()

    def set_dot(self, color: QColor | str | None) -> None:
        """Show a leading colour dot (e.g. project colour); None hides it."""
        self._dot = QColor(color) if color is not None else None
        self.updateGeometry()
        self.update()

    def set_clickable(self, clickable: bool) -> None:
        """Toggle click behaviour."""
        self._clickable = bool(clickable)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, clickable)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self._text) + 16
        if self._icon:
            w += 12 + 4
        if self._dot is not None:
            w += 6 + 6
        return QSize(max(20, w), 20)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def enterEvent(self, event: object) -> None:  # noqa: N802
        if self._clickable:
            self._hover = True
            self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            event.accept()
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        bg, fg = _tone_colors(self._tone)
        if self._hover and self._clickable:
            bg = theme.mix(bg, fg, 0.14)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        fill_rounded(p, r, r.height() / 2.0, bg)
        x = 8.0
        if self._dot is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._dot)
            p.drawEllipse(QRectF(x, (r.height() - 6) / 2.0, 6, 6))
            x += 12
        if self._icon:
            pm = icons.pixmap(self._icon, 12, fg, self.devicePixelRatioF())
            p.drawPixmap(int(x), int((r.height() - 12) / 2), pm)
            x += 16
        p.setPen(fg)
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, r.width() - x, r.height()), int(Qt.AlignmentFlag.AlignVCenter), self._text)
        p.end()


class Kbd(QWidget):
    """Keyboard shortcut rendered as key caps, e.g. ``Kbd("Ctrl+Shift+F")``."""

    def __init__(self, keys: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keys: list[str] = []
        self.setFont(fonts.font(11, 600))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_keys(keys)

    def set_keys(self, keys: str) -> None:
        """Set the shortcut text ("Ctrl+H"; a lone "+" key is supported as "Ctrl++")."""
        parts: list[str] = []
        buf = ""
        for i, ch in enumerate(keys):
            if ch == "+" and buf:
                parts.append(buf)
                buf = ""
            elif ch == "+" and not buf and i == len(keys) - 1:
                parts.append("+")
            else:
                buf += ch
        if buf:
            parts.append(buf)
        self._keys = [k.strip() for k in parts if k.strip()]
        self.setAccessibleName(" ".join(self._keys))
        self.updateGeometry()
        self.update()

    def _cap_widths(self) -> list[int]:
        fm = QFontMetrics(self.font())
        return [max(20, fm.horizontalAdvance(k) + 12) for k in self._keys]

    def sizeHint(self) -> QSize:  # noqa: N802
        widths = self._cap_widths()
        return QSize(sum(widths) + 4 * max(0, len(widths) - 1), 22)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(self.font())
        x = 0.0
        for key, w in zip(self._keys, self._cap_widths()):
            r = QRectF(x, 0, w, 21)
            fill_rounded(p, r, 5, pal.surface_alt, pal.border_strong)
            # bottom "keycap" lip
            p.setPen(theme.with_alpha(pal.border_strong, 0.9))
            p.drawLine(int(x + 4), 21, int(x + w - 4), 21)
            p.setPen(QColor(pal.text_muted))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), key)
            x += w + 4
        p.end()


class SectionHeader(QWidget):
    """Micro-caps section title with optional count and right-aligned action widget."""

    def __init__(
        self,
        title: str,
        count: int | str | None = None,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._title = Micro(title, self)
        self._count = Micro("", self, tone="subtle")
        lay.addWidget(self._title)
        lay.addWidget(self._count)
        lay.addStretch(1)
        self._action: QWidget | None = None
        self._layout = lay
        self.setMinimumHeight(28)
        self.set_count(count)
        if action is not None:
            self.set_action(action)

    def set_title(self, title: str) -> None:
        """Change the title."""
        self._title.setText(title)

    def set_count(self, count: int | str | None) -> None:
        """Show/hide the count next to the title."""
        if count is None or count == "":
            self._count.hide()
        else:
            self._count.setText(str(count))
            self._count.show()

    def set_action(self, widget: QWidget | None) -> None:
        """Replace the right-aligned action widget."""
        if self._action is not None:
            self._layout.removeWidget(self._action)
            self._action.setParent(None)  # type: ignore[call-overload]
        self._action = widget
        if widget is not None:
            self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
