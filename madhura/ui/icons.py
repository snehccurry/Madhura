"""Madhura's own stroke icon set, rendered crisply at any device pixel ratio.

Every glyph is a 24×24 SVG drawn with ``stroke-width 1.8``, round caps/joins, ``fill none`` and
``currentColor`` (substituted at render time). :class:`SvgIconEngine` renders at ``size × dpr`` so
icons stay sharp at 125–175 % scaling. Icon modes: Disabled → ``text_subtle``; Active/Selected →
``accent``; Normal → the requested colour (default ``text_muted``).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt, qVersion
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from madhura.ui import theme

__all__ = ["ICON_NAMES", "SvgIconEngine", "names", "has", "svg", "icon", "pixmap", "clear_cache"]


def _gear_path(teeth: int = 8, r_out: float = 9.6, r_in: float = 7.4) -> str:
    """Closed path for a cog wheel centred on (12, 12)."""
    step = 2 * math.pi / teeth
    half_top = step * 0.16
    half_base = step * 0.27
    pts: list[str] = []
    for i in range(teeth):
        c = -math.pi / 2 + i * step
        seq = (
            (r_in, c - half_base),
            (r_out, c - half_top),
            (r_out, c + half_top),
            (r_in, c + half_base),
        )
        for j, (r, a) in enumerate(seq):
            x, y = 12 + r * math.cos(a), 12 + r * math.sin(a)
            if i == 0 and j == 0:
                pts.append(f"M{x:.2f} {y:.2f}")
            elif j == 0:
                pts.append(f"A{r_in} {r_in} 0 0 1 {x:.2f} {y:.2f}")
            else:
                pts.append(f"L{x:.2f} {y:.2f}")
    a0 = -math.pi / 2 - half_base
    pts.append(f"A{r_in} {r_in} 0 0 1 {12 + r_in * math.cos(a0):.2f} {12 + r_in * math.sin(a0):.2f}Z")
    return "".join(pts)


def _dot(cx: float, cy: float, r: float = 1.25) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="currentColor" stroke="none"/>'


_EYE = "M2.8 12c2.1-4 5.4-6.5 9.2-6.5s7.1 2.5 9.2 6.5c-2.1 4-5.4 6.5-9.2 6.5S4.9 16 2.8 12z"
_BELL = '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.4 1.8H4.6z"/><path d="M10 20.8a2.1 2.1 0 0 0 4 0"/>'
_SPEAKER = '<path d="M4 9.4h3.2L11.5 5.8v12.4L7.2 14.6H4z"/>'

#: name → inner SVG markup on the 24×24 grid.
_ICONS: dict[str, str] = {
    "target": '<circle cx="12" cy="12" r="8.6"/><circle cx="12" cy="12" r="4.6"/>' + _dot(12, 12, 1.3),
    "check": '<path d="M5.2 12.6l4.4 4.4 9.2-9.6"/>',
    "check_circle": '<circle cx="12" cy="12" r="8.8"/><path d="M8.3 12.3l2.6 2.6 5-5.2"/>',
    "circle": '<circle cx="12" cy="12" r="8.8"/>',
    "repeat": (
        '<path d="M16.8 3.6l3 3-3 3"/><path d="M4.2 11.4v-.9a3.9 3.9 0 0 1 3.9-3.9h11.5"/>'
        '<path d="M7.2 20.4l-3-3 3-3"/><path d="M19.8 12.6v.9a3.9 3.9 0 0 1-3.9 3.9H4.4"/>'
    ),
    "chart": '<path d="M4 20h16"/><path d="M7 16.5v-4"/><path d="M12 16.5V7"/><path d="M17 16.5v-7"/>',
    "settings": f'<path d="{_gear_path()}"/><circle cx="12" cy="12" r="2.9"/>',
    "play": '<path d="M8 5.6v12.8a.8.8 0 0 0 1.2.7l10-6.4a.8.8 0 0 0 0-1.4l-10-6.4A.8.8 0 0 0 8 5.6z"/>',
    "pause": '<rect x="6.4" y="5.2" width="3.6" height="13.6" rx="1.1"/><rect x="14" y="5.2" width="3.6" height="13.6" rx="1.1"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.2"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "x": '<path d="M6.5 6.5l11 11"/><path d="M17.5 6.5l-11 11"/>',
    "more": _dot(5.5, 12, 1.5) + _dot(12, 12, 1.5) + _dot(18.5, 12, 1.5),
    "trash": (
        '<path d="M4.5 7h15"/><path d="M9.5 7V5.2c0-.7.5-1.2 1.2-1.2h2.6c.7 0 1.2.5 1.2 1.2V7"/>'
        '<path d="M6.5 7l.8 11.3a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9L17.5 7"/><path d="M10.2 11v5"/><path d="M13.8 11v5"/>'
    ),
    "edit": (
        '<path d="M4.6 19.4l.9-3.9L15.8 5.2a1.9 1.9 0 0 1 2.7 0l.3.3a1.9 1.9 0 0 1 0 2.7L8.5 18.5z"/>'
        '<path d="M14 7l3 3"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="8.8"/><path d="M12 7.2V12l3.1 1.9"/>',
    "flame": (
        '<path d="M12 21c-3.4 0-6-2.4-6-5.7 0-2.6 1.6-4.3 3-5.8.3 1.5 1 2.6 2.1 3.1-.3-3.2 1.1-6.3 3.7-8.6'
        ' .4 2.9 1.7 4.5 2.6 6 .6 1 .9 2.2.9 3.6C18.3 17.8 15.6 21 12 21z"/>'
    ),
    "bell": _BELL,
    "bell_off": (
        '<path d="M8.6 5.9A6 6 0 0 1 18 11v3.6"/><path d="M6 11v5.5l-1.4 1.8h11.9"/>'
        '<path d="M10 20.8a2.1 2.1 0 0 0 4 0"/><path d="M4 4l16 16"/>'
    ),
    "eye": f'<path d="{_EYE}"/><circle cx="12" cy="12" r="2.8"/>',
    "eye_off": (
        '<path d="M9.9 5.8A9.6 9.6 0 0 1 12 5.5c3.8 0 7.1 2.5 9.2 6.5a14 14 0 0 1-2.3 3.2"/>'
        '<path d="M15.6 17.5a8.6 8.6 0 0 1-3.6 1c-3.8 0-7.1-2.5-9.2-6.5a13.7 13.7 0 0 1 3.4-4.3"/>'
        '<path d="M10 10a2.8 2.8 0 0 0 4 4"/><path d="M4 4l16 16"/>'
    ),
    "expand": (
        '<path d="M14.5 4.5h5v5"/><path d="M19.5 4.5l-5.8 5.8"/>'
        '<path d="M9.5 19.5h-5v-5"/><path d="M4.5 19.5l5.8-5.8"/>'
    ),
    "sun": (
        '<circle cx="12" cy="12" r="3.9"/><path d="M12 2.8v1.9"/><path d="M12 19.3v1.9"/>'
        '<path d="M2.8 12h1.9"/><path d="M19.3 12h1.9"/><path d="M5.5 5.5l1.3 1.3"/>'
        '<path d="M17.2 17.2l1.3 1.3"/><path d="M5.5 18.5l1.3-1.3"/><path d="M17.2 6.8l1.3-1.3"/>'
    ),
    "moon": '<path d="M19.6 14.6A7.9 7.9 0 1 1 9.4 4.4a6.3 6.3 0 0 0 10.2 10.2z"/>',
    "monitor": '<rect x="3" y="4.5" width="18" height="12" rx="2.2"/><path d="M9 20h6"/><path d="M12 16.5V20"/>',
    "coffee": (
        '<path d="M4.6 9.4h11.2v4.4a5 5 0 0 1-5 5h-1.2a5 5 0 0 1-5-5z"/>'
        '<path d="M15.8 11h1.4a2.5 2.5 0 0 1 0 5h-1.6"/><path d="M8.2 3.6v2.6"/><path d="M11.8 3.6v2.6"/>'
    ),
    "calendar": (
        '<rect x="3.6" y="5" width="16.8" height="15.4" rx="2.4"/><path d="M3.6 9.8h16.8"/>'
        '<path d="M8.2 3v3.6"/><path d="M15.8 3v3.6"/>'
    ),
    "download": '<path d="M12 4v11"/><path d="M7.4 10.6L12 15.2l4.6-4.6"/><path d="M5 19.6h14"/>',
    "upload": '<path d="M12 15.2V4.2"/><path d="M7.4 8.6L12 4l4.6 4.6"/><path d="M5 19.6h14"/>',
    "folder": '<path d="M3.6 7.4a2 2 0 0 1 2-2h3.9l2 2.4h6.9a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2H5.6a2 2 0 0 1-2-2z"/>',
    "chevron_left": '<path d="M14.6 6l-6 6 6 6"/>',
    "chevron_right": '<path d="M9.4 6l6 6-6 6"/>',
    "chevron_down": '<path d="M6 9.4l6 6 6-6"/>',
    "chevron_up": '<path d="M6 14.6l6-6 6 6"/>',
    "keyboard": (
        '<rect x="2.6" y="5.8" width="18.8" height="12.4" rx="2.4"/>'
        '<path d="M6.6 9.9h.01"/><path d="M10.2 9.9h.01"/><path d="M13.8 9.9h.01"/><path d="M17.4 9.9h.01"/>'
        '<path d="M8.2 14.2h7.6"/>'
    ),
    "sparkle": (
        '<path d="M11 3.8c.6 4.1 2.7 6.2 6.8 6.8-4.1.6-6.2 2.7-6.8 6.8-.6-4.1-2.7-6.2-6.8-6.8 4.1-.6 6.2-2.7 6.8-6.8z"/>'
        '<path d="M18.3 15.6v4.2"/><path d="M16.2 17.7h4.2"/>'
    ),
    "note": (
        '<path d="M14.6 19.8H6a1.6 1.6 0 0 1-1.6-1.6V5.8A1.6 1.6 0 0 1 6 4.2h12a1.6 1.6 0 0 1 1.6 1.6v9z"/>'
        '<path d="M14.6 19.8v-3.6a1.2 1.2 0 0 1 1.2-1.2h3.8"/><path d="M8.2 9h7.6"/><path d="M8.2 12.4h4.4"/>'
    ),
    "skip": '<path d="M6.2 6.4v11.2a.7.7 0 0 0 1.1.6l7.8-5.6a.7.7 0 0 0 0-1.2L7.3 5.8a.7.7 0 0 0-1.1.6z"/><path d="M18.2 5.8v12.4"/>',
    "archive": (
        '<rect x="3.4" y="4.4" width="17.2" height="4.6" rx="1.3"/>'
        '<path d="M5.2 9v8.6a2 2 0 0 0 2 2h9.6a2 2 0 0 0 2-2V9"/><path d="M10 13h4"/>'
    ),
    "grip": "".join(_dot(x, y, 1.3) for y in (6, 12, 18) for x in (9, 15)),
    "info": '<circle cx="12" cy="12" r="8.8"/><path d="M12 11v5.2"/>' + _dot(12, 7.9, 1.15),
    "alert": (
        '<path d="M10.3 4.6L3.1 17.4A2 2 0 0 0 4.8 20.4h14.4a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0z"/>'
        '<path d="M12 9.6v4.2"/>' + _dot(12, 17, 1.15)
    ),
    "logo": (
        '<circle cx="12" cy="12" r="8.8"/>'
        '<circle cx="13.9" cy="10.1" r="3.4" fill="currentColor" stroke="none"/>'
    ),
    "arrow_right": '<path d="M4.8 12h14.4"/><path d="M13.4 6.2l5.8 5.8-5.8 5.8"/>',
    "sunrise": (
        '<path d="M3 18.6h18"/><path d="M7 18.6a5 5 0 0 1 10 0"/><path d="M12 3.4v6"/>'
        '<path d="M9.4 6L12 3.4 14.6 6"/><path d="M4.6 12.6l1.5 1.2"/><path d="M19.4 12.6l-1.5 1.2"/>'
    ),
    "sunset": (
        '<path d="M3 18.6h18"/><path d="M7 18.6a5 5 0 0 1 10 0"/><path d="M12 3.4v6"/>'
        '<path d="M9.4 6.8L12 9.4l2.6-2.6"/><path d="M4.6 12.6l1.5 1.2"/><path d="M19.4 12.6l-1.5 1.2"/>'
    ),
    "moon_star": (
        '<path d="M18.4 14.9A7.2 7.2 0 1 1 9.1 5.6a5.7 5.7 0 0 0 9.3 9.3z"/>'
        '<path d="M17.6 3.4v3.4"/><path d="M15.9 5.1h3.4"/>'
    ),
    "inbox": (
        '<path d="M3.6 13.4l2.5-7a1.6 1.6 0 0 1 1.5-1.1h8.8a1.6 1.6 0 0 1 1.5 1.1l2.5 7v4.9a1.6 1.6 0 0 1-1.6 1.6H5.2'
        'a1.6 1.6 0 0 1-1.6-1.6z"/><path d="M3.6 13.4h4.6l1.4 2.4h4.8l1.4-2.4h4.6"/>'
    ),
    "undo": '<path d="M8.8 14.2L4.4 9.8l4.4-4.4"/><path d="M4.6 9.8h10a5 5 0 0 1 0 10h-2.8"/>',
    "volume": _SPEAKER + '<path d="M15.4 9.2a4 4 0 0 1 0 5.6"/><path d="M18.2 6.6a7.6 7.6 0 0 1 0 10.8"/>',
    "volume_off": _SPEAKER + '<path d="M15.6 9.6l4.8 4.8"/><path d="M20.4 9.6l-4.8 4.8"/>',
    "power": '<path d="M12 3.6v7.6"/><path d="M7.1 6.6a7.6 7.6 0 1 0 9.8 0"/>',
    "list": (
        '<path d="M9 6.6h11"/><path d="M9 12h11"/><path d="M9 17.4h11"/>'
        + _dot(4.6, 6.6, 1.2)
        + _dot(4.6, 12, 1.2)
        + _dot(4.6, 17.4, 1.2)
    ),
    "flag": '<path d="M5.4 20.6V4.4"/><path d="M5.4 4.8h11.8l-2.2 4 2.2 4H5.4"/>',
    "search": '<circle cx="10.8" cy="10.8" r="6.2"/><path d="M15.4 15.4l4.2 4.2"/>',
    "dot": _dot(12, 12, 3),
}

#: Every available icon name (sorted).
ICON_NAMES: tuple[str, ...] = tuple(sorted(_ICONS))

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

_pixmap_cache: dict[tuple[str, str, int, int, float], QPixmap] = {}
_renderer_cache: dict[tuple[str, str], QSvgRenderer] = {}


def names() -> list[str]:
    """All icon names."""
    return list(ICON_NAMES)


def has(name: str) -> bool:
    """True if ``name`` is a known icon."""
    return name in _ICONS


def clear_cache() -> None:
    """Drop all rendered pixmaps (called on theme change)."""
    _pixmap_cache.clear()
    _renderer_cache.clear()


def _color_str(c: QColor | str | None) -> QColor:
    if c is None:
        return QColor(theme.current().palette.text_muted)
    q = QColor(c)
    if not q.isValid():
        return QColor(theme.current().palette.text_muted)
    return q


def svg(name: str, color: QColor | str = "currentColor") -> str:
    """Return the SVG source for ``name`` with ``currentColor`` replaced by ``color``."""
    body = _ICONS[name]  # KeyError for unknown names (by design)
    doc = _TEMPLATE.format(body=body)
    if color != "currentColor":
        doc = doc.replace("currentColor", QColor(color).name(QColor.NameFormat.HexRgb))
    return doc


def _renderer(name: str, rgb: str) -> QSvgRenderer:
    key = (name, rgb)
    r = _renderer_cache.get(key)
    if r is None:
        r = QSvgRenderer(QByteArray(svg(name, rgb).encode("utf-8")))
        _renderer_cache[key] = r
    return r


def _logical_scaled() -> bool:
    """True when QIcon hands QIconEngine.scaledPixmap a logical size (Qt 6.8+)."""
    try:
        major, minor = (int(x) for x in qVersion().split(".")[:2])
    except ValueError:
        return True
    return (major, minor) >= (6, 8)


def _default_dpr() -> float:
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    return float(app.devicePixelRatio())  # type: ignore[attr-defined]


def pixmap(
    name: str,
    size: int | QSize = 18,
    color: QColor | str | None = None,
    dpr: float | None = None,
) -> QPixmap:
    """Render icon ``name`` at logical ``size`` (px) and device pixel ratio ``dpr``.

    The returned pixmap has ``devicePixelRatio == dpr`` so it can be drawn at logical size.
    Raises ``KeyError`` for unknown names.
    """
    if name not in _ICONS:
        raise KeyError(name)
    if isinstance(size, QSize):
        w, h = size.width(), size.height()
    else:
        w = h = int(size)
    ratio = float(dpr) if dpr else _default_dpr()
    c = _color_str(color)
    key = (name, c.name(QColor.NameFormat.HexArgb), w, h, round(ratio, 3))
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached
    pw, ph = max(1, int(round(w * ratio))), max(1, int(round(h * ratio)))
    img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if c.alphaF() < 1.0:
        painter.setOpacity(c.alphaF())
    side = min(pw, ph)
    target = QRectF((pw - side) / 2.0, (ph - side) / 2.0, side, side)
    _renderer(name, c.name(QColor.NameFormat.HexRgb)).render(painter, target)
    painter.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(ratio)
    if len(_pixmap_cache) > 2048:
        _pixmap_cache.clear()
    _pixmap_cache[key] = pm
    return pm


class SvgIconEngine(QIconEngine):
    """QIconEngine that renders a Madhura glyph at the exact device-pixel size requested."""

    def __init__(self, name: str, color: QColor | str | None = None, size: int | None = None) -> None:
        super().__init__()
        if name not in _ICONS:
            raise KeyError(name)
        self._name = name
        self._color = QColor(color) if color is not None else None
        self._size = size

    def _color_for(self, mode: QIcon.Mode, state: QIcon.State) -> QColor:
        pal = theme.current().palette
        if mode == QIcon.Mode.Disabled:
            return QColor(pal.text_subtle)
        if mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
            return QColor(pal.accent)
        return QColor(self._color) if self._color is not None else QColor(pal.text_muted)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        device = painter.device()
        ratio = device.devicePixelRatioF() if device is not None else _default_dpr()
        side = min(rect.width(), rect.height())
        pm = pixmap(self._name, side, self._color_for(mode, state), ratio)
        x = rect.x() + (rect.width() - side) // 2
        y = rect.y() + (rect.height() - side) // 2
        painter.drawPixmap(QRect(x, y, side, side), pm)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        pm = pixmap(self._name, size, self._color_for(mode, state), 1.0)
        return QPixmap(pm)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        # Qt >= 6.8 passes the logical size (older versions passed size * scale).
        phys = QSize(round(size.width() * scale), round(size.height() * scale)) if _logical_scaled() else size
        pm = QPixmap(pixmap(self._name, phys, self._color_for(mode, state), 1.0))
        pm.setDevicePixelRatio(scale)
        return pm

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        side = min(size.width(), size.height())
        return QSize(side, side)

    def availableSizes(self, mode: QIcon.Mode = QIcon.Mode.Normal, state: QIcon.State = QIcon.State.Off) -> list[QSize]:
        s = self._size or 24
        return [QSize(s, s)]

    def clone(self) -> QIconEngine:
        return SvgIconEngine(self._name, self._color, self._size)

    def key(self) -> str:
        return "MadhuraSvgIconEngine"

    def iconName(self) -> str:
        return self._name

    def isNull(self) -> bool:
        return False


def icon(name: str, color: QColor | str | None = None, size: int | None = None) -> QIcon:
    """Return a QIcon for ``name`` (``KeyError`` if unknown). ``color`` defaults to ``text_muted``
    resolved at paint time, so default-coloured icons follow theme changes automatically."""
    return QIcon(SvgIconEngine(name, color, size))
