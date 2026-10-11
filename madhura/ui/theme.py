"""Design tokens, palettes, the application stylesheet and the ThemeManager.

Colour tokens are plain strings (``"#RRGGBB"`` or ``"#AARRGGBB"``) so they can be dropped into QSS
f-strings *and* passed straight to QPainter / QColor APIs (PySide6 converts them implicitly). Use
:meth:`Palette.color` (or :func:`color`) when you need a ``QColor`` to manipulate.
"""

from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass, fields
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from madhura.ui import fonts

__all__ = [
    "THEMES",
    "ACCENTS",
    "ACCENT_NAMES",
    "PROJECT_HUES",
    "Palette",
    "ThemeManager",
    "build_palette",
    "build_stylesheet",
    "build_qpalette",
    "current",
    "set_current",
    "palette",
    "color",
    "with_alpha",
    "mix",
    "set_prop",
    "project_color",
]

THEMES: tuple[str, ...] = ("system", "dark", "light")

#: accent name → (dark variant, light variant)
ACCENTS: dict[str, tuple[str, str]] = {
    "indigo": ("#7A86FF", "#4F5BD5"),
    "teal": ("#2EC4B6", "#0D7A71"),
    "rose": ("#FF6B8B", "#D6335A"),
    "amber": ("#FFB547", "#996200"),
    "violet": ("#B38CFF", "#7A4FD8"),
}
ACCENT_NAMES: tuple[str, ...] = tuple(ACCENTS)

_BASE: dict[str, dict[str, Any]] = {
    "dark": {
        "bg": "#0E0F13",
        "sidebar": "#0B0C0F",
        "surface": "#16181D",
        "surface_alt": "#1C1F26",
        "hover": "#23262E",
        "border": "#262A33",
        "border_strong": "#343945",
        "track": "#343945",
        "text": "#ECEEF2",
        "text_muted": "#A0A6B2",
        "text_subtle": "#858B99",
        "success": "#3CCB8F",
        "warning": "#F2B84B",
        "danger": "#F0616D",
        "break_": "#64B5F6",
        "overlay": "#9E0E0F13",  # rgba(14,15,19,0.62)
        "shadow_alpha": 0.45,
        "accent_text": "#0E0F13",
        "input": "#121419",
        "panel": "#1C1F26",
        "panel_border": "#343945",
    },
    "light": {
        "bg": "#F5F6F8",
        "sidebar": "#EEF0F3",
        "surface": "#FFFFFF",
        "surface_alt": "#F3F4F7",
        "hover": "#EBEDF1",
        "border": "#E2E5EA",
        "border_strong": "#CDD2DA",
        "track": "#DDE1E7",
        "text": "#14161B",
        "text_muted": "#5A6170",
        "text_subtle": "#6B7280",
        "success": "#167A55",
        "warning": "#9A6700",
        "danger": "#C93A46",
        "break_": "#1F6FB2",
        "overlay": "#6B14161B",  # rgba(20,22,27,0.42)
        "shadow_alpha": 0.14,
        "accent_text": "#FFFFFF",
        "input": "#FFFFFF",
        "panel": "#FFFFFF",
        "panel_border": "#E2E5EA",
    },
}

#: Eight muted hues used for project colours (deterministic by name).
PROJECT_HUES: tuple[int, ...] = (228, 172, 142, 38, 18, 340, 272, 200)


# --------------------------------------------------------------------------- colour helpers


def _hex(c: QColor) -> str:
    if c.alpha() == 255:
        return c.name(QColor.NameFormat.HexRgb).upper()
    return c.name(QColor.NameFormat.HexArgb).upper()


def with_alpha(c: QColor | str, alpha: float) -> QColor:
    """Return a copy of ``c`` with alpha ``alpha`` (0..1)."""
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return out


def mix(a: QColor | str, b: QColor | str, t: float) -> QColor:
    """Linear blend: ``t = 0`` → ``a``, ``t = 1`` → ``b`` (alpha blended too)."""
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, float(t)))
    return QColor.fromRgbF(
        ca.redF() + (cb.redF() - ca.redF()) * t,
        ca.greenF() + (cb.greenF() - ca.greenF()) * t,
        ca.blueF() + (cb.blueF() - ca.blueF()) * t,
        ca.alphaF() + (cb.alphaF() - ca.alphaF()) * t,
    )


def _shift_lightness(c: str, delta: float) -> str:
    q = QColor(c)
    h, s, lightness, a = q.getHslF()
    lightness = max(0.0, min(1.0, lightness + delta))
    return _hex(QColor.fromHslF(max(0.0, h), s, lightness, a))


# --------------------------------------------------------------------------- palette


@dataclass(frozen=True)
class Palette:
    """A fully resolved set of colour tokens for one scheme + accent (spec §8)."""

    name: str
    accent_name: str
    bg: str
    sidebar: str
    surface: str
    surface_alt: str
    hover: str
    border: str
    border_strong: str
    track: str
    text: str
    text_muted: str
    text_subtle: str
    success: str
    warning: str
    danger: str
    break_: str
    overlay: str
    shadow_alpha: float
    accent_text: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_border: str
    danger_soft: str
    success_soft: str
    warning_soft: str
    break_soft: str
    selection: str
    input: str
    panel: str
    panel_border: str

    @property
    def is_dark(self) -> bool:
        """True for the dark scheme."""
        return self.name == "dark"

    def color(self, token: str, alpha: float | None = None) -> QColor:
        """Return token ``token`` as a QColor, optionally with alpha overridden (0..1)."""
        value = getattr(self, token)
        c = QColor(value)
        if alpha is not None:
            c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c

    def as_dict(self) -> dict[str, Any]:
        """All tokens as a plain dict."""
        return asdict(self)

    @staticmethod
    def token_names() -> list[str]:
        """Names of the colour tokens (excludes ``name``, ``accent_name``, ``shadow_alpha``)."""
        skip = {"name", "accent_name", "shadow_alpha"}
        return [f.name for f in fields(Palette) if f.name not in skip]


def build_palette(scheme: str = "dark", accent: str = "indigo") -> Palette:
    """Resolve the palette for ``scheme`` ("dark"/"light") and ``accent`` name."""
    scheme = "light" if scheme == "light" else "dark"
    accent = accent if accent in ACCENTS else "indigo"
    base = dict(_BASE[scheme])
    dark = scheme == "dark"
    acc = ACCENTS[accent][0 if dark else 1]
    sign = 1.0 if dark else -1.0
    surface = base["surface"]
    return Palette(
        name=scheme,
        accent_name=accent,
        accent=acc,
        accent_hover=_shift_lightness(acc, 0.08 * sign),
        accent_pressed=_shift_lightness(acc, 0.14 * sign),
        accent_soft=_hex(with_alpha(acc, 0.16)),
        accent_border=_hex(with_alpha(acc, 0.55)),
        danger_soft=_hex(with_alpha(base["danger"], 0.16)),
        success_soft=_hex(with_alpha(base["success"], 0.16)),
        warning_soft=_hex(with_alpha(base["warning"], 0.16)),
        break_soft=_hex(with_alpha(base["break_"], 0.16)),
        selection=_hex(mix(surface, acc, 0.38 if dark else 0.24)),
        **base,
    )


def project_color(name: str | None, palette: Palette | None = None) -> str:
    """Deterministic muted colour for a project name (theme-aware; never stored)."""
    pal = palette or current().palette
    key = (name or "").strip().lower()
    if not key:
        return pal.text_subtle
    hue = PROJECT_HUES[zlib.crc32(key.encode("utf-8")) % len(PROJECT_HUES)]
    if pal.is_dark:
        c = QColor.fromHsl(hue, int(255 * 0.52), int(255 * 0.66))
    else:
        c = QColor.fromHsl(hue, int(255 * 0.50), int(255 * 0.44))
    return _hex(c)


# --------------------------------------------------------------------------- QPalette + QSS


def build_qpalette(p: Palette) -> QPalette:
    """A QPalette mirroring the tokens so Fusion-drawn bits match the design."""
    qp = QPalette()
    roles = QPalette.ColorRole
    groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled)
    for g in groups:
        disabled = g == QPalette.ColorGroup.Disabled
        txt = p.text_subtle if disabled else p.text
        qp.setColor(g, roles.Window, QColor(p.bg))
        qp.setColor(g, roles.WindowText, QColor(txt))
        qp.setColor(g, roles.Base, QColor(p.input))
        qp.setColor(g, roles.AlternateBase, QColor(p.surface_alt))
        qp.setColor(g, roles.Text, QColor(txt))
        qp.setColor(g, roles.Button, QColor(p.surface_alt))
        qp.setColor(g, roles.ButtonText, QColor(txt))
        qp.setColor(g, roles.BrightText, QColor(p.text))
        qp.setColor(g, roles.Highlight, QColor(p.selection))
        qp.setColor(g, roles.HighlightedText, QColor(p.text))
        qp.setColor(g, roles.ToolTipBase, QColor(p.surface_alt))
        qp.setColor(g, roles.ToolTipText, QColor(p.text))
        qp.setColor(g, roles.PlaceholderText, QColor(p.text_subtle))
        qp.setColor(g, roles.Link, QColor(p.accent))
        qp.setColor(g, roles.LinkVisited, QColor(p.accent))
        qp.setColor(g, roles.Light, QColor(p.hover))
        qp.setColor(g, roles.Midlight, QColor(p.border_strong))
        qp.setColor(g, roles.Mid, QColor(p.border_strong))
        qp.setColor(g, roles.Dark, QColor(p.border))
        qp.setColor(g, roles.Shadow, QColor("#000000"))
        if hasattr(roles, "Accent"):
            qp.setColor(g, roles.Accent, QColor(p.accent))
    return qp


def _ff(size: int) -> str:
    return f'font-family: "{fonts.family_for(size)}"; font-size: {size}px;'


def build_stylesheet(p: Palette) -> str:
    """Generate the application-wide QSS for palette ``p`` (spec §8)."""
    panel = p.panel
    handle = "#FFFFFF"
    handle_border = p.border_strong if not p.is_dark else "#00000000"
    return f"""
* {{ outline: none; }}
QMainWindow {{ background-color: {p.bg}; }}
QToolTip {{
    background-color: {p.surface_alt}; color: {p.text}; border: 1px solid {p.border_strong};
    padding: 5px 8px; border-radius: 0px; font-size: 12px;
}}

/* ---------------------------------------------------------------- typography roles */
QLabel {{ background: transparent; }}
QLabel[role="display"] {{ {_ff(60)} font-weight: 300; color: {p.text}; }}
QLabel[role="display_sm"] {{ {_ff(48)} font-weight: 300; color: {p.text}; }}
QLabel[role="h1"] {{ {_ff(24)} font-weight: 600; color: {p.text}; }}
QLabel[role="h2"] {{ {_ff(16)} font-weight: 600; color: {p.text}; }}
QLabel[role="body"] {{ {_ff(14)} font-weight: 400; color: {p.text}; }}
QLabel[role="label"] {{ {_ff(13)} font-weight: 600; color: {p.text}; }}
QLabel[role="caption"] {{ {_ff(12)} font-weight: 400; color: {p.text_muted}; }}
QLabel[role="micro"] {{ {_ff(11)} font-weight: 600; color: {p.text_muted}; }}
QLabel[role="muted"] {{ color: {p.text_muted}; }}
QLabel[role="subtle"] {{ color: {p.text_subtle}; }}
QLabel[tone="muted"] {{ color: {p.text_muted}; }}
QLabel[tone="subtle"] {{ color: {p.text_subtle}; }}
QLabel[tone="accent"] {{ color: {p.accent}; }}
QLabel[tone="success"] {{ color: {p.success}; }}
QLabel[tone="warning"] {{ color: {p.warning}; }}
QLabel[tone="danger"] {{ color: {p.danger}; }}
QLabel[tone="break"] {{ color: {p.break_}; }}
QLabel:disabled {{ color: {p.text_subtle}; }}

/* ---------------------------------------------------------------- plain buttons (fallback) */
QPushButton, QToolButton {{
    background-color: {p.surface_alt}; color: {p.text}; border: 1px solid {p.border_strong};
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover, QToolButton:hover {{ background-color: {p.hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background-color: {p.border}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {p.text_subtle}; }}
QPushButton[variant="primary"] {{ background-color: {p.accent}; color: {p.accent_text}; border: none; }}
QPushButton[variant="primary"]:hover {{ background-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {p.accent_pressed}; }}
QPushButton[variant="ghost"] {{ background-color: transparent; color: {p.text_muted}; border: none; }}
QPushButton[variant="ghost"]:hover {{ background-color: {p.hover}; color: {p.text}; }}
QPushButton[variant="danger"] {{ background-color: {p.danger_soft}; color: {p.danger}; border: none; }}
QPushButton[variant="danger_solid"] {{ background-color: {p.danger}; color: #FFFFFF; border: none; }}

/* ---------------------------------------------------------------- inputs */
QLineEdit, QAbstractSpinBox, QComboBox {{
    background-color: {p.input}; color: {p.text}; border: 1px solid {p.border_strong};
    border-radius: 8px; padding: 0px 10px; min-height: 34px;
    selection-background-color: {p.selection}; selection-color: {p.text};
}}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{ border-color: {p.text_subtle}; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border: 2px solid {p.accent}; padding: 0px 9px; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color: {p.text_subtle}; border-color: {p.border}; }}
QLineEdit[error="true"] {{ border-color: {p.danger}; }}
QLineEdit[error="true"]:focus {{ border: 2px solid {p.danger}; }}
QLineEdit#mhInlineEdit, QLineEdit#mhInlineEdit:focus, QLineEdit#mhInlineEdit:hover,
QAbstractSpinBox#mhInlineSpin, QAbstractSpinBox#mhInlineSpin:focus, QAbstractSpinBox#mhInlineSpin:hover {{
    background: transparent; border: none; padding: 0px; min-height: 0px; margin: 0px;
}}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QPlainTextEdit, QTextEdit {{
    background-color: {p.input}; color: {p.text}; border: 1px solid {p.border_strong};
    border-radius: 8px; padding: 6px 8px;
    selection-background-color: {p.selection}; selection-color: {p.text};
}}
QPlainTextEdit:hover, QTextEdit:hover {{ border-color: {p.text_subtle}; }}
QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {p.accent}; padding: 5px 7px; }}
QPlainTextEdit:disabled, QTextEdit:disabled {{ color: {p.text_subtle}; border-color: {p.border}; }}

/* ---------------------------------------------------------------- scrolling */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; border: none; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; border: none; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:hover, QScrollBar::handle:pressed {{ background: {p.text_subtle}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------------------------------------------------------------- slider */
QSlider {{ background: transparent; min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: {p.track}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {p.track}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {handle}; border: 1px solid {handle_border}; width: 14px; height: 14px;
    margin: -6px 0px; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {handle}; border: 1px solid {p.accent}; }}
QSlider::sub-page:horizontal:disabled {{ background: {p.text_subtle}; }}
QSlider::handle:horizontal:disabled {{ background: {p.border_strong}; }}

/* ---------------------------------------------------------------- progress */
QProgressBar {{ background: {p.track}; border: none; border-radius: 2px; max-height: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 2px; }}

/* ---------------------------------------------------------------- menus */
QMenu {{
    background-color: {panel}; color: {p.text}; border: 1px solid {p.panel_border};
    border-radius: 10px; padding: 6px; font-size: 13px;
}}
QMenu[nativeRound="true"] {{ border-radius: 0px; border: none; }}
QMenu::item {{ padding: 0px 14px 0px 8px; min-height: 32px; border-radius: 6px; background: transparent; }}
QMenu::item:selected {{ background-color: {p.hover}; color: {p.text}; }}
QMenu::item:disabled {{ color: {p.text_subtle}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 6px 6px; }}
QMenu::icon {{ padding-left: 6px; }}
QMenu::indicator {{ width: 16px; height: 16px; image: none; border: none; background: transparent; }}

/* ---------------------------------------------------------------- item views / completer popups */
QAbstractItemView {{
    background-color: {panel}; color: {p.text}; border: 1px solid {p.panel_border};
    border-radius: 8px; padding: 4px; outline: 0px;
    selection-background-color: {p.hover}; selection-color: {p.text};
}}
QAbstractItemView::item {{ min-height: 30px; padding: 0px 8px; border-radius: 6px; }}
QAbstractItemView::item:hover {{ background-color: {p.hover}; }}
QAbstractItemView::item:selected {{ background-color: {p.hover}; color: {p.text}; }}
QHeaderView::section {{ background: transparent; color: {p.text_muted}; border: none; padding: 4px; }}
"""


# --------------------------------------------------------------------------- dynamic properties


def set_prop(widget: QWidget, name: str, value: Any) -> None:
    """Set a QSS-relevant dynamic property and re-polish the widget so the style updates."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


# --------------------------------------------------------------------------- manager

_current: "ThemeManager | None" = None


def _system_scheme() -> str:
    """The OS colour scheme as "dark"/"light" (unknown → dark, Madhura is dark-first)."""
    if QGuiApplication.instance() is None:
        return "dark"
    scheme = QGuiApplication.styleHints().colorScheme()
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


def set_current(manager: "ThemeManager | None") -> None:
    """Make ``manager`` the active ThemeManager (None → a dark default is created on demand)."""
    global _current
    _current = manager


def current() -> "ThemeManager":
    """The active ThemeManager (last constructed/applied). Creates a dark default if none exists."""
    global _current
    if _current is None:
        _current = ThemeManager()
    return _current


def palette() -> Palette:
    """Shortcut for ``current().palette``."""
    return current().palette


def color(token: str, alpha: float | None = None) -> QColor:
    """Shortcut for ``current().palette.color(token, alpha)``."""
    return current().palette.color(token, alpha)


class ThemeManager(QObject):
    """Owns the theme/accent/motion preferences and applies palette + QSS to the application.

    ``changed`` fires whenever the resolved palette changes (theme, accent or — for the
    ``"system"`` theme — the OS colour scheme). ``motion_changed(bool)`` fires when
    reduce-motion toggles.
    """

    changed = Signal()
    motion_changed = Signal(bool)

    def __init__(
        self,
        theme: str = "dark",
        accent: str = "indigo",
        reduce_motion: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        global _current
        self._theme = theme if theme in THEMES else "dark"
        self._accent = accent if accent in ACCENTS else "indigo"
        self._reduce_motion = bool(reduce_motion)
        self._app: QApplication | None = None
        self._hints_connected = False
        self._scheme = self._resolve_scheme()
        self._palette = build_palette(self._scheme, self._accent)
        self._qss: str | None = None
        _current = self

    # ------------------------------------------------------------------ properties
    @property
    def theme(self) -> str:
        """Configured theme: "system", "dark" or "light"."""
        return self._theme

    @property
    def accent(self) -> str:
        """Configured accent name."""
        return self._accent

    @property
    def reduce_motion(self) -> bool:
        """True when animations should be skipped (or reduced to opacity)."""
        return self._reduce_motion

    @property
    def scheme(self) -> str:
        """Resolved scheme: "dark" or "light"."""
        return self._scheme

    @property
    def is_dark(self) -> bool:
        """True when the resolved scheme is dark."""
        return self._scheme == "dark"

    @property
    def palette(self) -> Palette:
        """The resolved :class:`Palette`."""
        return self._palette

    def stylesheet(self) -> str:
        """The application QSS for the current palette (cached)."""
        if self._qss is None:
            self._qss = build_stylesheet(self._palette)
        return self._qss

    # ------------------------------------------------------------------ setters
    def set_theme(self, name: str) -> None:
        """Switch between "system", "dark" and "light"."""
        if name not in THEMES:
            raise ValueError(f"unknown theme {name!r}")
        if name == self._theme:
            return
        self._theme = name
        if self._app is not None:
            self._apply_scheme_hint()
        self._refresh()

    def set_accent(self, name: str) -> None:
        """Switch the accent colour (one of :data:`ACCENT_NAMES`)."""
        if name not in ACCENTS:
            raise ValueError(f"unknown accent {name!r}")
        if name == self._accent:
            return
        self._accent = name
        self._refresh()

    def set_reduce_motion(self, enabled: bool) -> None:
        """Enable/disable reduced motion."""
        enabled = bool(enabled)
        if enabled == self._reduce_motion:
            return
        self._reduce_motion = enabled
        self.motion_changed.emit(enabled)

    # ------------------------------------------------------------------ application
    def apply(self, app: QApplication | None = None) -> None:
        """Apply colour-scheme hint, QPalette and QSS to ``app`` (default: the running app)."""
        global _current
        _current = self
        app = app or QApplication.instance()  # type: ignore[assignment]
        if app is None:
            return
        self._app = app
        self._connect_hints()
        self._apply_scheme_hint()
        new_scheme = self._resolve_scheme()
        if new_scheme != self._scheme:
            self._scheme = new_scheme
            self._palette = build_palette(self._scheme, self._accent)
            self._qss = None
        self._apply_to_app()

    def _apply_to_app(self) -> None:
        app = self._app
        if app is None:
            return
        app.setPalette(build_qpalette(self._palette))
        app.setStyleSheet(self.stylesheet())

    def _apply_scheme_hint(self) -> None:
        hints = QGuiApplication.styleHints()
        if not hasattr(hints, "setColorScheme"):
            return
        target = {
            "dark": Qt.ColorScheme.Dark,
            "light": Qt.ColorScheme.Light,
        }.get(self._theme, Qt.ColorScheme.Unknown)
        try:
            hints.setColorScheme(target)
        except (RuntimeError, TypeError):
            pass

    def _connect_hints(self) -> None:
        if self._hints_connected or QGuiApplication.instance() is None:
            return
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
            self._hints_connected = True

    def _on_system_scheme_changed(self, *_args: object) -> None:
        if self._theme == "system":
            self._refresh()

    def _resolve_scheme(self) -> str:
        if self._theme in ("dark", "light"):
            return self._theme
        return _system_scheme()

    def _refresh(self) -> None:
        scheme = self._resolve_scheme()
        new_palette = build_palette(scheme, self._accent)
        if new_palette == self._palette and scheme == self._scheme:
            return
        self._scheme = scheme
        self._palette = new_palette
        self._qss = None
        try:
            from madhura.ui import icons  # local import: icons depends on theme

            icons.clear_cache()
        except ImportError:
            pass
        if self._app is not None:
            self._apply_to_app()
        self.changed.emit()
