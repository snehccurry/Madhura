"""UI kit: theme tokens, ThemeManager, QSS, fonts, icons and animation helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QColor, QFont, QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget  # noqa: E402

from madhura.ui import animations, fonts, icons, theme  # noqa: E402

SPEC_TOKENS = [
    "bg", "sidebar", "surface", "surface_alt", "hover", "border", "border_strong", "track", "text",
    "text_muted", "text_subtle", "success", "warning", "danger", "break_", "overlay", "shadow_alpha",
    "accent_text", "accent", "accent_hover", "accent_pressed", "accent_soft", "danger_soft",
]

SPEC_ICONS = (
    "target check check_circle circle repeat chart settings play pause stop plus minus x more trash edit "
    "clock flame bell bell_off eye_off expand sun moon monitor coffee calendar download upload folder "
    "chevron_left chevron_right chevron_down chevron_up keyboard sparkle note skip archive grip info alert "
    "logo arrow_right sunrise sunset moon_star inbox undo volume volume_off power list flag"
).split()


@pytest.fixture(autouse=True)
def _theme(qapp: QApplication) -> Iterator[theme.ThemeManager]:
    tm = theme.ThemeManager("dark", "indigo", reduce_motion=True)
    yield tm
    theme.set_current(None)
    qapp.setStyleSheet("")


# --------------------------------------------------------------------------- palette


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_palette_has_every_spec_token(scheme: str) -> None:
    pal = theme.build_palette(scheme, "indigo")
    for name in SPEC_TOKENS:
        assert hasattr(pal, name), name
        if name != "shadow_alpha":
            assert QColor(getattr(pal, name)).isValid(), name
    assert pal.is_dark == (scheme == "dark")


def test_exact_spec_values() -> None:
    dark = theme.build_palette("dark", "indigo")
    light = theme.build_palette("light", "teal")
    assert dark.bg == "#0E0F13" and dark.accent == "#7A86FF" and dark.shadow_alpha == pytest.approx(0.45)
    assert light.surface == "#FFFFFF" and light.accent == "#0D7A71" and light.shadow_alpha == pytest.approx(0.14)
    assert QColor(dark.overlay).alphaF() == pytest.approx(0.62, abs=0.01)
    assert QColor(light.overlay).alphaF() == pytest.approx(0.42, abs=0.01)


def test_derived_accent_tokens() -> None:
    dark = theme.build_palette("dark", "indigo")
    light = theme.build_palette("light", "indigo")
    assert QColor(dark.accent_hover).lightnessF() > QColor(dark.accent).lightnessF()
    assert QColor(light.accent_hover).lightnessF() < QColor(light.accent).lightnessF()
    assert QColor(dark.accent_pressed).lightnessF() > QColor(dark.accent_hover).lightnessF()
    assert QColor(dark.accent_soft).alphaF() == pytest.approx(0.16, abs=0.01)
    assert QColor(dark.danger_soft).alphaF() == pytest.approx(0.16, abs=0.01)
    assert QColor(dark.accent_soft).rgb() == QColor(dark.accent).rgb()


def test_unknown_accent_falls_back() -> None:
    assert theme.build_palette("dark", "nope").accent_name == "indigo"


def test_project_color_deterministic_and_theme_aware() -> None:
    dark = theme.build_palette("dark")
    light = theme.build_palette("light")
    a1 = theme.project_color("Madhura", dark)
    assert a1 == theme.project_color("  madhura ", dark)
    assert a1 != theme.project_color("Madhura", light)
    colors = {theme.project_color(f"project {i}", dark) for i in range(200)}
    assert len(colors) == 8
    assert theme.project_color("", dark) == dark.text_subtle
    assert theme.project_color(None, dark) == dark.text_subtle


# --------------------------------------------------------------------------- manager


def test_manager_standalone_and_current(_theme: theme.ThemeManager) -> None:
    assert theme.current() is _theme
    assert _theme.palette.name == "dark"
    other = theme.ThemeManager("light", "rose")
    assert theme.current() is other
    assert other.is_dark is False and other.palette.accent == "#D6335A"


def test_set_theme_regenerates_qss_and_emits(qapp: QApplication, _theme: theme.ThemeManager) -> None:
    _theme.apply(qapp)
    dark_qss = qapp.styleSheet()
    assert "#0E0F13" in dark_qss
    seen: list[int] = []
    _theme.changed.connect(lambda: seen.append(1))
    _theme.set_theme("light")
    assert seen == [1]
    assert qapp.styleSheet() != dark_qss
    assert "#F5F6F8" in qapp.styleSheet()
    assert qapp.palette().color(qapp.palette().ColorRole.Window) == QColor("#F5F6F8")
    _theme.set_theme("light")  # no-op
    assert seen == [1]


def test_set_accent_and_validation(_theme: theme.ThemeManager) -> None:
    seen: list[int] = []
    _theme.changed.connect(lambda: seen.append(1))
    _theme.set_accent("teal")
    assert _theme.palette.accent == "#2EC4B6" and seen == [1]
    with pytest.raises(ValueError):
        _theme.set_accent("magenta")
    with pytest.raises(ValueError):
        _theme.set_theme("sepia")


def test_reduce_motion_signal_and_motion_enabled(_theme: theme.ThemeManager) -> None:
    got: list[bool] = []
    _theme.motion_changed.connect(got.append)
    assert animations.motion_enabled() is False
    _theme.set_reduce_motion(False)
    assert got == [False] and animations.motion_enabled() is True


def test_system_theme_follows_os(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    monkeypatch.setattr(theme, "_system_scheme", lambda: "light")
    tm = theme.ThemeManager("system")
    tm.apply(qapp)
    assert tm.scheme == "light"
    changed: list[int] = []
    tm.changed.connect(lambda: changed.append(1))
    monkeypatch.setattr(theme, "_system_scheme", lambda: "dark")
    tm._on_system_scheme_changed()  # what QStyleHints.colorSchemeChanged triggers
    assert tm.scheme == "dark" and changed == [1]
    tm.set_theme("light")
    monkeypatch.setattr(theme, "_system_scheme", lambda: "dark")
    tm._on_system_scheme_changed()
    assert tm.scheme == "light"  # explicit theme ignores the OS


def test_stylesheet_contains_roles_and_variants() -> None:
    qss = theme.build_stylesheet(theme.build_palette("dark"))
    for role in ("h1", "h2", "body", "label", "caption", "micro", "muted", "subtle"):
        assert f'QLabel[role="{role}"]' in qss
    for variant in ("primary", "ghost", "danger", "danger_solid"):
        assert f'[variant="{variant}"]' in qss
    for needle in ("QScrollBar:vertical", "QToolTip", "QSlider::groove", "QMenu::item", "QAbstractItemView", "QScrollArea"):
        assert needle in qss


def test_set_prop_repolishes(qapp: QApplication, _theme: theme.ThemeManager) -> None:
    _theme.apply(qapp)
    label = QLabel("x")
    label.ensurePolished()
    theme.set_prop(label, "tone", "danger")
    assert label.property("tone") == "danger"
    assert label.palette().color(label.foregroundRole()) == QColor(_theme.palette.danger)
    theme.set_prop(label, "tone", "success")
    assert label.palette().color(label.foregroundRole()) == QColor(_theme.palette.success)


# --------------------------------------------------------------------------- fonts


def test_fonts() -> None:
    f = fonts.font(14)
    assert f.pixelSize() == 14 and f.weight() == QFont.Weight.Normal
    assert fonts.font(13, 600).weight() == QFont.Weight.DemiBold
    assert fonts.family_for(24) == fonts.families().display
    assert fonts.family_for(12) == fonts.families().text
    micro = fonts.role_font("micro")
    assert micro.pixelSize() == 11 and micro.letterSpacing() == pytest.approx(0.6, abs=0.05)
    d = fonts.display_font(60)
    assert d.pixelSize() == 60 and d.weight() == QFont.Weight.Light
    if hasattr(QFont, "Tag"):
        assert fonts.font(13, tabular=True).featureValue(QFont.Tag("tnum")) == 1
    assert fonts.reserve_width(fonts.font(13), "00:00") > 0


def test_segoe_weight_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fonts, "_cache", fonts.FontFamilies("Segoe UI", "Segoe UI", "segoe"))
    assert fonts.font(13, 500).weight() == QFont.Weight.DemiBold
    monkeypatch.setattr(fonts, "_cache", fonts.FontFamilies("Inter", "Inter", "inter"))
    assert fonts.font(13, 500).weight() == QFont.Weight.Medium


# --------------------------------------------------------------------------- icons


def _opaque_pixels(img: QImage) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if QColor.fromRgba(img.pixel(x, y)).alpha() > 40:
                n += 1
    return n


@pytest.mark.parametrize("name", SPEC_ICONS)
def test_every_required_icon_renders(name: str) -> None:
    assert name in icons.ICON_NAMES
    pm = icons.pixmap(name, 24, "#FFFFFF", 1.0)
    assert pm.width() == 24 and not pm.isNull()
    assert _opaque_pixels(pm.toImage()) > 6
    hi = icons.pixmap(name, 16, "#FFFFFF", 2.0)
    assert hi.width() == 32 and hi.devicePixelRatio() == 2.0


def test_unknown_icon_raises() -> None:
    with pytest.raises(KeyError):
        icons.icon("definitely-not-an-icon")
    with pytest.raises(KeyError):
        icons.pixmap("definitely-not-an-icon", 16)


def _dominant(img: QImage) -> QColor:
    best = QColor(0, 0, 0, 0)
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor.fromRgba(img.pixel(x, y))
            if c.alpha() > best.alpha():
                best = c
    return best


def test_icon_modes_and_cache(_theme: theme.ThemeManager) -> None:
    ic = icons.icon("check", "#FF0000")
    pal = _theme.palette
    normal = _dominant(ic.pixmap(QSize(24, 24), 1.0, QIcon.Mode.Normal).toImage())
    disabled = _dominant(ic.pixmap(QSize(24, 24), 1.0, QIcon.Mode.Disabled).toImage())
    active = _dominant(ic.pixmap(QSize(24, 24), 1.0, QIcon.Mode.Active).toImage())
    assert normal.red() > 200 and normal.green() < 40
    assert disabled.rgb() == QColor(pal.text_subtle).rgb()
    assert active.rgb() == QColor(pal.accent).rgb()
    icons.pixmap("plus", 16, None, 1.0)
    assert icons._pixmap_cache  # noqa: SLF001
    _theme.set_theme("light")
    assert not icons._pixmap_cache  # noqa: SLF001 - cleared on theme change


def test_icon_caches_stay_bounded_under_hover_fades(_theme: theme.ThemeManager) -> None:
    """Hover fades ask for a new interpolated colour every frame: the SVG is parsed once per icon
    and the tinted pixmaps live in a bounded LRU (no unbounded growth until the next theme
    change), while static icons in use survive the churn."""
    icons.clear_cache()
    pal = _theme.palette
    static = icons.pixmap("logo", 18, pal.accent, 1.0)
    for sweep in range(12):
        for name in ("target", "repeat", "chart", "settings"):
            for i in range(200):
                icons.pixmap(name, 18, theme.mix(pal.text_muted, pal.text, i / 199), 1.0)
            assert icons.pixmap("logo", 18, pal.accent, 1.0) is static  # recently used: kept
    assert len(icons._renderer_cache) <= len(icons.ICON_NAMES)  # noqa: SLF001 - one per icon
    assert len(icons._pixmap_cache) <= icons._PIXMAP_LIMIT  # noqa: SLF001
    assert len(icons._mask_cache) <= 5  # noqa: SLF001 - one per (icon, device size)


def test_tinted_icon_matches_the_svg_in_that_colour() -> None:
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    for name, size, dpr, color in (("check", 18, 1.0, "#7A86FF"), ("pause", 16, 1.5, "#A0A6B2"), ("settings", 14, 2.0, "#FFFFFF")):
        side = round(size * dpr)
        ref = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        ref.fill(Qt.GlobalColor.transparent)
        p = QPainter(ref)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        QSvgRenderer(QByteArray(icons.svg(name, color).encode())).render(p, QRectF(0, 0, side, side))
        p.end()
        got = icons.pixmap(name, size, color, dpr).toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        assert got.size() == ref.size()
        worst = max(
            abs(((ref.pixel(x, y) >> s) & 255) - ((got.pixel(x, y) >> s) & 255))
            for y in range(side) for x in range(side) for s in (0, 8, 16, 24)
        )
        assert worst <= 2, (name, worst)


def test_icon_scaled_pixmap_is_crisp() -> None:
    pm = icons.icon("target").pixmap(QSize(18, 18), 1.5)
    assert pm.width() == 27


# --------------------------------------------------------------------------- animations


def test_reduce_motion_sets_immediately(qtbot) -> None:  # type: ignore[no-untyped-def]
    w = QWidget()
    qtbot.addWidget(w)
    done: list[int] = []
    assert animations.animate(w, "minimumWidth", 77, on_finished=lambda: done.append(1)) is None
    assert w.minimumWidth() == 77 and done == [1]
    vals: list[float] = []
    assert animations.tween(0, 10, 300, vals.append) is None and vals == [10.0]


def test_fades_remove_effect(qtbot, _theme: theme.ThemeManager) -> None:  # type: ignore[no-untyped-def]
    _theme.set_reduce_motion(False)
    parent = QWidget()
    qtbot.addWidget(parent)
    child = QLabel("hello", parent)
    parent.show()
    anim = animations.fade_in(child, 60)
    assert anim is not None and child.graphicsEffect() is not None
    qtbot.waitUntil(lambda: child.graphicsEffect() is None, timeout=2000)
    finished: list[int] = []
    animations.fade_out(child, 60, on_finished=lambda: finished.append(1))
    qtbot.waitUntil(lambda: bool(finished), timeout=2000)
    assert child.isHidden() and child.graphicsEffect() is None


def test_interrupted_fade_removes_effect(qtbot, _theme: theme.ThemeManager) -> None:  # type: ignore[no-untyped-def]
    _theme.set_reduce_motion(False)
    parent = QWidget()
    qtbot.addWidget(parent)
    child = QLabel("x", parent)
    parent.show()
    animations.fade_in(child, 5000)
    animations.stop_animations(child)
    assert child.graphicsEffect() is None


def test_cross_fade(qtbot, _theme: theme.ThemeManager) -> None:  # type: ignore[no-untyped-def]
    _theme.set_reduce_motion(False)
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    a, b = QLabel("a"), QLabel("b")
    stack.addWidget(a)
    stack.addWidget(b)
    stack.show()
    done: list[int] = []
    animations.cross_fade(stack, 1, 60, on_finished=lambda: done.append(1))
    assert stack.currentIndex() == 1
    qtbot.waitUntil(lambda: bool(done), timeout=2000)
    assert b.graphicsEffect() is None
