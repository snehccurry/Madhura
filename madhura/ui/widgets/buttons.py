"""Buttons: :class:`Button` (5 variants, 3 sizes), :class:`IconButton` and :class:`LinkButton`.

All are custom-painted ``QPushButton`` subclasses: hover/press fade, crisp icons tinted to the text
colour, and a 2 px accent focus ring that appears only for keyboard focus.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QEnterEvent, QFontMetrics, QPainter, QPaintEvent
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from madhura.ui import fonts, icons, theme
from madhura.ui.widgets._common import HoverFader, KeyboardFocusMixin, fill_rounded

__all__ = ["VARIANTS", "SIZES", "Button", "IconButton", "LinkButton"]

VARIANTS = ("primary", "secondary", "ghost", "danger", "danger_solid")

#: size → (height, horizontal padding, font px, icon px)
SIZES: dict[str, tuple[int, int, int, int]] = {
    "sm": (28, 10, 12, 14),
    "md": (36, 14, 13, 16),
    "lg": (40, 16, 14, 16),
}

_RADIUS = 8.0
_ICON_GAP = 8


def _variant_colors(variant: str, hover: float, pressed: bool) -> tuple[QColor, QColor | None, QColor]:
    """Return (background, border or None, foreground) for a variant at a hover amount."""
    p = theme.palette()
    if variant == "primary":
        bg = QColor(p.accent_pressed) if pressed else theme.mix(p.accent, p.accent_hover, hover)
        return bg, None, QColor(p.accent_text)
    if variant == "ghost":
        bg = theme.with_alpha(p.hover, (1.0 if pressed else hover))
        if pressed:
            bg = theme.mix(p.hover, p.border_strong, 0.35)
        fg = theme.mix(p.text_muted, p.text, hover)
        return bg, None, fg
    if variant == "danger":
        base = QColor(p.danger_soft)
        strong = theme.with_alpha(p.danger, 0.26 if pressed else 0.22)
        return theme.mix(base, strong, 1.0 if pressed else hover), None, QColor(p.danger)
    if variant == "danger_solid":
        light = QColor(p.danger).lighter(112) if p.is_dark else QColor(p.danger).darker(110)
        bg = QColor(p.danger).darker(112) if pressed else theme.mix(p.danger, light, hover)
        return bg, None, QColor("#FFFFFF")
    # secondary
    bg = theme.mix(p.surface_alt, p.hover, 1.0 if pressed else hover)
    if pressed:
        bg = theme.mix(p.hover, p.border_strong, 0.3)
    return bg, QColor(p.border_strong), QColor(p.text)


class Button(KeyboardFocusMixin, QPushButton):
    """Text button with optional leading icon.

    ``variant``: primary | secondary | ghost | danger | danger_solid.
    ``size``: "sm" (28) | "md" (36) | "lg" (40).
    """

    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        icon: str | None = None,
        size: str = "md",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._variant = variant if variant in VARIANTS else "secondary"
        self._size = size if size in SIZES else "md"
        self._icon_name: str | None = None
        self._hover = HoverFader(self, 120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("variant", self._variant)
        self._ring_radius = _RADIUS
        self._apply_size()
        if icon:
            self.set_icon_name(icon)

    # ------------------------------------------------------------------ API
    def variant(self) -> str:
        """Current variant name."""
        return self._variant

    def set_variant(self, variant: str) -> None:
        """Change the variant (primary/secondary/ghost/danger/danger_solid)."""
        if variant not in VARIANTS or variant == self._variant:
            return
        self._variant = variant
        theme.set_prop(self, "variant", variant)
        self.update()

    def button_size(self) -> str:
        """Size key ("sm", "md", "lg")."""
        return self._size

    def set_button_size(self, size: str) -> None:
        """Change the size key."""
        if size in SIZES and size != self._size:
            self._size = size
            self._apply_size()

    def icon_name(self) -> str | None:
        """Name of the leading icon (or None)."""
        return self._icon_name

    def set_icon_name(self, name: str | None) -> None:
        """Set/clear the leading icon by name (``KeyError`` for unknown names)."""
        if name is not None and not icons.has(name):
            raise KeyError(name)
        self._icon_name = name
        self.updateGeometry()
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.updateGeometry()

    # ------------------------------------------------------------------ sizing
    def _apply_size(self) -> None:
        h, _pad, fpx, _ipx = SIZES[self._size]
        self.setFont(fonts.font(fpx, 600))
        self.setFixedHeight(h)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        h, pad, _fpx, ipx = SIZES[self._size]
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) if self.text() else 0
        if self._icon_name:
            w += ipx + (_ICON_GAP if self.text() else 0)
        w += 2 * pad if self.text() else 2 * ((h - ipx) // 2)
        return QSize(max(w, h), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    # ------------------------------------------------------------------ events
    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def changeEvent(self, event: object) -> None:  # noqa: N802
        super().changeEvent(event)  # type: ignore[arg-type]
        if not self.isEnabled():
            self._hover.set_target(0.0, animate=False)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        pressed = self.isDown() or self.isChecked()
        bg, border, fg = _variant_colors(self._variant, self._hover.value, pressed)
        rect = QRectF(self.rect())
        fill_rounded(p, rect, _RADIUS, bg, border)
        h, _pad, _fpx, ipx = SIZES[self._size]
        fm = QFontMetrics(self.font())
        text = self.text()
        tw = fm.horizontalAdvance(text) if text else 0
        content_w = tw + ((ipx + (_ICON_GAP if text else 0)) if self._icon_name else 0)
        x = (self.width() - content_w) / 2.0
        if self._icon_name:
            pm = icons.pixmap(self._icon_name, ipx, fg, self.devicePixelRatioF())
            p.drawPixmap(int(round(x)), int(round((self.height() - ipx) / 2.0)), pm)
            x += ipx + (_ICON_GAP if text else 0)
        if text:
            p.setPen(fg)
            p.setFont(self.font())
            text_rect = QRectF(x, 0, tw + 1, self.height())
            p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.end()


class IconButton(KeyboardFocusMixin, QPushButton):
    """Square icon-only button. ``tooltip`` is mandatory; the accessible name defaults to the
    tooltip text before any " · shortcut" suffix.

    ``variant``: "ghost" (default, transparent until hover), "secondary" (bordered) or "primary".
    Checkable icon buttons show the accent colour when checked.
    """

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        size: int = 32,
        parent: QWidget | None = None,
        *,
        accessible_name: str | None = None,
        glyph: int | None = None,
        variant: str = "ghost",
        checkable: bool = False,
        color: QColor | str | None = None,
    ) -> None:
        super().__init__(parent)
        if not icons.has(icon_name):
            raise KeyError(icon_name)
        self._icon_name = icon_name
        self._variant = variant
        self._color = QColor(color) if color is not None else None
        self._glyph = glyph or (18 if size >= 32 else 16 if size >= 24 else max(10, int(size * 0.6)))
        self._hover = HoverFader(self, 120)
        self._ring_radius = 8.0 if size >= 28 else size / 2.0
        self.setFixedSize(size, size)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible_name or tooltip.split(" · ")[0].strip())

    def icon_name(self) -> str:
        """Current icon name."""
        return self._icon_name

    def set_icon_name(self, name: str) -> None:
        """Swap the glyph (e.g. pause ↔ play)."""
        if not icons.has(name):
            raise KeyError(name)
        self._icon_name = name
        self.update()

    def set_color(self, color: QColor | str | None) -> None:
        """Override the resting glyph colour (None → muted text)."""
        self._color = QColor(color) if color is not None else None
        self.update()

    def set_tooltip(self, tooltip: str, accessible_name: str | None = None) -> None:
        """Update tooltip and accessible name together."""
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible_name or tooltip.split(" · ")[0].strip())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width(), self.height())

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        t = self._hover.value
        rect = QRectF(self.rect())
        radius = 8.0 if self.width() >= 28 else self.width() / 2.0
        if self._variant == "primary":
            bg = QColor(pal.accent_pressed) if self.isDown() else theme.mix(pal.accent, pal.accent_hover, t)
            fill_rounded(p, rect, radius, bg)
            fg = QColor(pal.accent_text)
        else:
            if self._variant == "secondary":
                bg = theme.mix(pal.surface_alt, pal.hover, t)
                fill_rounded(p, rect, radius, bg, pal.border_strong)
            elif self.isDown():
                fill_rounded(p, rect, radius, theme.mix(pal.hover, pal.border_strong, 0.35))
            elif self.isChecked():
                fill_rounded(p, rect, radius, pal.accent_soft)
            elif t > 0:
                fill_rounded(p, rect, radius, theme.with_alpha(pal.hover, t))
            rest = self._color if self._color is not None else QColor(pal.text_muted)
            fg = QColor(pal.accent) if self.isChecked() else theme.mix(rest, pal.text, t)
        g = self._glyph
        pm = icons.pixmap(self._icon_name, g, fg, self.devicePixelRatioF())
        p.drawPixmap(int((self.width() - g) / 2), int((self.height() - g) / 2), pm)
        p.end()


class LinkButton(KeyboardFocusMixin, QPushButton):
    """Text-only quiet action ("Not a focus block?", "Resume nudges", "Wrap up the day").

    ``tone``: "muted" (default; text_muted → text on hover), "accent" or "danger".
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        tone: str = "muted",
        icon: str | None = None,
        size_px: int = 13,
        weight: int = 500,
    ) -> None:
        super().__init__(text, parent)
        self._tone = tone
        self._icon_name = icon
        self._hover = HoverFader(self, 120)
        self._ring_radius = 6.0
        self.setFont(fonts.font(size_px, weight))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        """Change the colour tone (muted/accent/danger)."""
        self._tone = tone
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) + 8
        if self._icon_name:
            w += 14 + 6
        return QSize(w, max(24, fm.height() + 6))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hover.set_target(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hover.set_target(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def _fg(self) -> QColor:
        pal = theme.palette()
        t = self._hover.value
        if self._tone == "accent":
            return theme.mix(pal.accent, pal.accent_hover, t)
        if self._tone == "danger":
            return QColor(pal.danger)
        return theme.mix(pal.text_muted, pal.text, t)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            p.setOpacity(0.45)
        fg = self._fg()
        x = 4
        if self._icon_name:
            pm = icons.pixmap(self._icon_name, 14, fg, self.devicePixelRatioF())
            p.drawPixmap(x, (self.height() - 14) // 2, pm)
            x += 20
        p.setPen(fg)
        p.setFont(self.font())
        p.drawText(QRectF(x, 0, self.width() - x, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text())
        if self._hover.value > 0.5 and self._tone != "muted":
            fm = QFontMetrics(self.font())
            y = (self.height() + fm.ascent()) / 2.0 + 2
            p.setPen(theme.with_alpha(fg, 0.5))
            p.drawLine(int(x), int(y), int(x + fm.horizontalAdvance(self.text())), int(y))
        p.end()
