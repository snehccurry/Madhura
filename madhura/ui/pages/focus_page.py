"""STUB — owned by [P1]. FocusPage: idle/active stack, banners and session-event routing (spec §1.2–1.4).

Created by the app shell [F3]; the focus-idle agent replaces it. Methods the shell relies on
(keep them): ``start_from_input()``, ``focus_task_input()``, ``set_nudge_banner(visible)``,
``on_shown()``; Space toggles pause in ``keyPressEvent`` when no text input/button has focus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QStackedWidget, QTextEdit, QVBoxLayout, QWidget

from madhura.ui import animations
from madhura.ui.overlays import session_complete
from madhura.ui.pages.focus.active_view import ActiveView
from madhura.ui.pages.focus.idle_view import IdleView

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["FocusPage"]

_TEXT_INPUTS = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QAbstractButton)


class FocusPage(QWidget):
    """The Focus page (``ctx.window`` page key "focus"). STUB."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(self)
        self.idle_view = IdleView(ctx, self._stack)
        self.active_view = ActiveView(ctx, self._stack)
        self._stack.addWidget(self.idle_view)
        self._stack.addWidget(self.active_view)
        lay.addWidget(self._stack)
        ctx.engine.state_changed.connect(self._on_state)
        ctx.engine.session_finished.connect(self._on_session_finished)
        ctx.engine.break_finished.connect(self._on_break_finished)
        ctx.events.data_changed.connect(self._on_data_changed)
        ctx.events.day_changed.connect(self._on_data_changed)
        self._sync(animate=False)

    # ------------------------------------------------------------------ routing

    def _on_state(self, _state: str) -> None:
        self._sync(animate=True)

    def _sync(self, animate: bool) -> None:
        index = 0 if str(self._ctx.engine.state) == "idle" else 1
        if animate:
            animations.cross_fade(self._stack, index, 220)
        else:
            self._stack.setCurrentIndex(index)
        if index == 0:
            self.idle_view.refresh()

    def _on_session_finished(self, session_id: int, outcome: str, restored: bool) -> None:
        session_complete.present_session_complete(self._ctx, session_id, outcome, restored)

    def _on_break_finished(self, natural: bool) -> None:
        if not natural:
            return
        self._ctx.sound.play("break_over")
        window = self._ctx.window
        if window is not None and not window.is_main_visible():
            self._ctx.notifier.notify("Break's over", "Plan your next block")

    def _on_data_changed(self, *_args: object) -> None:
        if self.isVisible():
            self.idle_view.refresh()

    # ------------------------------------------------------------------ shell API

    def on_shown(self) -> None:
        """Called by the main window whenever this page becomes visible."""
        self.idle_view.refresh()

    def start_from_input(self) -> int | None:
        """Ctrl+Enter: start a session from the plan card (idle/break only)."""
        if self._ctx.engine.is_active:
            return None
        return self.idle_view.plan_card.start()

    def focus_task_input(self) -> None:
        """Focus the task field (only while idle)."""
        if str(self._ctx.engine.state) == "idle":
            self.idle_view.plan_card.focus_task()

    def set_nudge_banner(self, visible: bool) -> None:
        """Show/hide the firm-nudge banner."""
        self.idle_view.set_nudge_banner(visible)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            fw = QApplication.focusWidget()
            if not isinstance(fw, _TEXT_INPUTS) and self._ctx.engine.is_active:
                self._ctx.engine.toggle_pause()
                event.accept()
                return
        super().keyPressEvent(event)
