"""Small, safe motion helpers.

Rules (spec §7.2/§8): 160–240 ms OutCubic for UI motion; with reduce-motion everything is either
instant or an opacity-only fade; temporary ``QGraphicsEffect``s are always removed when an animation
ends (or is interrupted) so text never stays rendered through an effect. Top-level windows fade via
``windowOpacity`` instead of effects.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QVariantAnimation,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget

from madhura.ui import theme

__all__ = [
    "DURATION_FAST",
    "DURATION",
    "DURATION_SLOW",
    "motion_enabled",
    "animate",
    "tween",
    "fade_in",
    "fade_out",
    "slide",
    "cross_fade",
    "stop_animations",
]

DURATION_FAST = 160
DURATION = 200
DURATION_SLOW = 240

Curve = QEasingCurve.Type
_ANIM_ATTR = "_mh_animations"


def motion_enabled() -> bool:
    """False when the active ThemeManager has reduce-motion on."""
    return not theme.current().reduce_motion


def _track(target: QObject, key: str, anim: QAbstractAnimation | None) -> None:
    """Remember the running animation for ``key`` on ``target`` (stopping a previous one)."""
    store: dict[str, QAbstractAnimation] | None = getattr(target, _ANIM_ATTR, None)
    if store is None:
        store = {}
        try:
            setattr(target, _ANIM_ATTR, store)
        except AttributeError:
            return
    prev = store.pop(key, None)
    if prev is not None and prev is not anim:
        try:
            if prev.state() != QAbstractAnimation.State.Stopped:
                prev.stop()
        except RuntimeError:
            pass
    if anim is not None:
        store[key] = anim


def stop_animations(target: QObject) -> None:
    """Stop every helper-managed animation running on ``target``."""
    store: dict[str, QAbstractAnimation] | None = getattr(target, _ANIM_ATTR, None)
    if not store:
        return
    for anim in list(store.values()):
        try:
            anim.stop()
        except RuntimeError:
            pass
    store.clear()


def animate(
    obj: QObject,
    prop: str | bytes,
    end: Any,
    ms: int = DURATION,
    curve: Curve = QEasingCurve.Type.OutCubic,
    *,
    start: Any = None,
    on_finished: Callable[[], None] | None = None,
    force: bool = False,
) -> QPropertyAnimation | None:
    """Animate Qt property ``prop`` of ``obj`` to ``end``.

    With reduce-motion (and ``force`` False) the property is set immediately, ``on_finished`` is
    called synchronously and ``None`` is returned. Starting a new animation of the same property
    stops the previous one.
    """
    name = prop.encode() if isinstance(prop, str) else bytes(prop)
    key = "prop:" + name.decode()
    if not force and (not motion_enabled() or ms <= 0):
        _track(obj, key, None)
        obj.setProperty(name.decode(), end)
        if on_finished is not None:
            on_finished()
        return None
    anim = QPropertyAnimation(obj, name, obj)
    anim.setDuration(int(ms))
    anim.setEasingCurve(curve)
    if start is not None:
        anim.setStartValue(start)
    anim.setEndValue(end)
    if on_finished is not None:
        anim.finished.connect(on_finished)
    _track(obj, key, anim)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def tween(
    start: float,
    end: float,
    ms: int,
    on_value: Callable[[float], None],
    *,
    curve: Curve = QEasingCurve.Type.OutCubic,
    on_finished: Callable[[], None] | None = None,
    parent: QObject | None = None,
    key: str | None = None,
) -> QVariantAnimation | None:
    """Drive ``on_value`` from ``start`` to ``end`` (e.g. count-up numbers).

    Reduce-motion → ``on_value(end)`` immediately. ``parent`` keeps the animation alive and, with
    ``key``, lets a later tween on the same parent replace this one.
    """
    if not motion_enabled() or ms <= 0:
        if parent is not None and key:
            _track(parent, "tween:" + key, None)
        on_value(end)
        if on_finished is not None:
            on_finished()
        return None
    anim = QVariantAnimation(parent)
    anim.setDuration(int(ms))
    anim.setEasingCurve(curve)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.valueChanged.connect(lambda v: on_value(float(v)))
    if on_finished is not None:
        anim.finished.connect(on_finished)
    if parent is not None and key:
        _track(parent, "tween:" + key, anim)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def _remove_effect(widget: QWidget, effect: QGraphicsOpacityEffect) -> None:
    try:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
    except RuntimeError:
        pass


def _fade(
    widget: QWidget,
    start: float,
    end: float,
    ms: int,
    curve: Curve,
    on_finished: Callable[[], None] | None,
) -> QAbstractAnimation | None:
    if widget.isWindow():
        widget.setWindowOpacity(start)
        return animate(widget, "windowOpacity", end, ms, curve, start=start, on_finished=on_finished, force=True)
    stop_animations_key(widget, "fade")
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(int(ms))
    anim.setEasingCurve(curve)
    anim.setStartValue(start)
    anim.setEndValue(end)

    def _done() -> None:
        _remove_effect(widget, effect)
        if on_finished is not None:
            on_finished()

    anim.finished.connect(_done)
    # If the animation is stopped early (replaced/destroyed), still drop the effect.
    anim.stateChanged.connect(
        lambda new, _old: _remove_effect(widget, effect) if new == QAbstractAnimation.State.Stopped else None
    )
    _track(widget, "fade", anim)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def stop_animations_key(target: QObject, key: str) -> None:
    """Stop the helper-managed animation stored under ``key`` (if any)."""
    _track(target, key, None)


def fade_in(
    widget: QWidget,
    ms: int = DURATION,
    *,
    curve: Curve = QEasingCurve.Type.OutCubic,
    on_finished: Callable[[], None] | None = None,
) -> QAbstractAnimation | None:
    """Show ``widget`` and fade it in (top-levels via windowOpacity, children via a temporary effect)."""
    widget.show()
    if not motion_enabled():
        if widget.isWindow():
            widget.setWindowOpacity(1.0)
        if on_finished is not None:
            on_finished()
        return None
    return _fade(widget, 0.0, 1.0, ms, curve, on_finished)


def fade_out(
    widget: QWidget,
    ms: int = DURATION_FAST,
    *,
    curve: Curve = QEasingCurve.Type.InCubic,
    hide: bool = True,
    on_finished: Callable[[], None] | None = None,
) -> QAbstractAnimation | None:
    """Fade ``widget`` out, then hide it (unless ``hide`` is False) and call ``on_finished``."""

    def _done() -> None:
        if hide:
            widget.hide()
            if widget.isWindow():
                widget.setWindowOpacity(1.0)
        if on_finished is not None:
            on_finished()

    if not motion_enabled() or not widget.isVisible():
        _done()
        return None
    start = widget.windowOpacity() if widget.isWindow() else 1.0
    return _fade(widget, start, 0.0, ms, curve, _done)


def slide(
    widget: QWidget,
    start: QPoint | None,
    end: QPoint,
    ms: int = DURATION,
    curve: Curve = QEasingCurve.Type.OutCubic,
    *,
    on_finished: Callable[[], None] | None = None,
) -> QPropertyAnimation | None:
    """Move ``widget`` from ``start`` (default: current pos) to ``end``."""
    if start is not None:
        widget.move(start)
    return animate(widget, "pos", end, ms, curve, on_finished=on_finished)


def cross_fade(
    stack: QStackedWidget,
    index: int,
    ms: int = 220,
    *,
    on_finished: Callable[[], None] | None = None,
) -> None:
    """Switch ``stack`` to page ``index`` with a short fade-in of the new page.

    The opacity effect lives only for the duration of the animation.
    """
    if index == stack.currentIndex():
        if on_finished is not None:
            on_finished()
        return
    stack.setCurrentIndex(index)
    page = stack.currentWidget()
    if page is None or not motion_enabled() or not stack.isVisible():
        if on_finished is not None:
            on_finished()
        return
    _fade(page, 0.0, 1.0, ms, QEasingCurve.Type.OutCubic, on_finished)

