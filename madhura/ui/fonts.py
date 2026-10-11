"""Font family resolution and the Madhura type scale.

Family preference (spec §8):

* Windows 11 → "Segoe UI Variable Display" for sizes ≥ 20 px, "Segoe UI Variable Text" below.
* Windows 10 → "Segoe UI" (weight 500 is mapped to 600 because the static family has no Medium).
* Elsewhere → "Inter" (plus "Inter Display" for large sizes), then the system default. Inter is
  bundled in ``madhura/assets/fonts`` (SIL OFL 1.1) and registered on first use, so Linux and
  macOS get the typography the layouts were designed for even when Inter isn't installed.

All sizes are in *pixels* so text scales with the device pixel ratio, not the font DPI setting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QGuiApplication

from madhura import paths

log = logging.getLogger(__name__)

__all__ = [
    "DISPLAY_THRESHOLD_PX",
    "TYPE_SCALE",
    "FontFamilies",
    "families",
    "family_for",
    "font",
    "display_font",
    "role_font",
    "set_tabular",
    "apply_app_font",
    "register_bundled_fonts",
    "reserve_width",
    "reset_cache",
]

#: Text at or above this pixel size uses the "display" optical family.
DISPLAY_THRESHOLD_PX = 20

#: role → (pixel size, weight). ``micro`` is rendered uppercase with 0.6 px letter spacing.
TYPE_SCALE: dict[str, tuple[int, int]] = {
    "display": (60, 300),
    "display_sm": (48, 300),
    "h1": (24, 600),
    "h2": (16, 600),
    "body": (14, 400),
    "label": (13, 600),
    "caption": (12, 400),
    "micro": (11, 600),
}

MICRO_LETTER_SPACING = 0.6


@dataclass(frozen=True)
class FontFamilies:
    """Resolved font families for the running platform."""

    text: str
    display: str
    kind: str  # "segoe_variable" | "segoe" | "inter" | "default"


_cache: FontFamilies | None = None
_bundled_registered = False


def register_bundled_fonts() -> int:
    """Register the bundled Inter fonts with Qt (once per process); returns how many loaded.

    Needs a QGuiApplication. Missing or unreadable files are logged and skipped, so a build
    without the fonts (the Windows one, which uses Segoe UI) simply falls back.
    """
    global _bundled_registered
    if _bundled_registered or QGuiApplication.instance() is None:
        return 0
    _bundled_registered = True
    folder = paths.asset("fonts")
    loaded = 0
    for path in sorted(folder.glob("*.otf")) if folder.is_dir() else []:
        if QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded += 1
        else:
            log.warning("Could not load bundled font %s", path.name)
    return loaded


def reset_cache() -> None:
    """Forget the resolved families (tests, or after registering application fonts)."""
    global _cache
    _cache = None


def families() -> FontFamilies:
    """Return the resolved font families (cached once a QGuiApplication exists)."""
    global _cache
    if _cache is not None:
        return _cache
    if QGuiApplication.instance() is None:
        # No font database yet: answer optimistically without caching.
        return FontFamilies("Inter", "Inter", "inter")
    available = set(QFontDatabase.families())
    if "Segoe UI" not in available and register_bundled_fonts():
        available = set(QFontDatabase.families())
    if "Segoe UI Variable Text" in available and "Segoe UI Variable Display" in available:
        result = FontFamilies("Segoe UI Variable Text", "Segoe UI Variable Display", "segoe_variable")
    elif "Segoe UI" in available:
        result = FontFamilies("Segoe UI", "Segoe UI", "segoe")
    elif "Inter" in available:
        display = "Inter Display" if "Inter Display" in available else "Inter"
        result = FontFamilies("Inter", display, "inter")
    else:
        default = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        result = FontFamilies(default, default, "default")
    _cache = result
    return result


def family_for(size_px: float) -> str:
    """Return the family to use for text of ``size_px`` pixels."""
    fams = families()
    return fams.display if size_px >= DISPLAY_THRESHOLD_PX else fams.text


def _map_weight(weight: int) -> int:
    w = max(100, min(900, int(round(weight / 100.0)) * 100))
    if families().kind == "segoe" and w == 500:
        return 600
    return w


def set_tabular(f: QFont, enabled: bool = True) -> QFont:
    """Enable tabular (fixed-width) digits on ``f`` when Qt supports font features."""
    tag_cls = getattr(QFont, "Tag", None)
    if tag_cls is not None and hasattr(f, "setFeature"):
        try:
            if enabled:
                f.setFeature(tag_cls("tnum"), 1)
            elif hasattr(f, "unsetFeature"):
                f.unsetFeature(tag_cls("tnum"))
        except (TypeError, ValueError, AttributeError):
            pass
    return f


def font(size_px: float, weight: int = 400, tabular: bool = False, *, family: str | None = None) -> QFont:
    """Build a QFont with a pixel size, CSS-like weight and optional tabular digits."""
    f = QFont(family or family_for(size_px))
    f.setPixelSize(max(1, int(round(size_px))))
    f.setWeight(QFont.Weight(_map_weight(weight)))
    if tabular:
        set_tabular(f)
    return f


def display_font(size_px: float, weight: int = 300, tabular: bool = True) -> QFont:
    """Large display font (timer digits, KPI values). Tabular digits by default."""
    return font(size_px, weight, tabular, family=families().display)


def role_font(role: str, tabular: bool = False) -> QFont:
    """Return the font for a type-scale role (``h1``, ``caption``, ``micro`` …)."""
    size, weight = TYPE_SCALE.get(role, TYPE_SCALE["body"])
    f = font(size, weight, tabular)
    if role == "micro":
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, MICRO_LETTER_SPACING)
    return f


def reserve_width(f: QFont, sample: str = "00:00") -> int:
    """Pixel width needed to show ``sample`` in ``f`` (use to stop timers from jittering)."""
    return QFontMetrics(f).horizontalAdvance(sample)


def apply_app_font(app: QGuiApplication) -> None:
    """Set the application-wide default font (body 14 px)."""
    reset_cache()
    app.setFont(font(14, 400))
