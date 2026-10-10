"""Focus page container [P1]: idle ↔ active stack, page-level banners and session-event routing
(spec §1.2–§1.4, §1.7, §1.13, §5.2).

* ``IdleView`` (planning) and P2's ``ActiveView`` (running / paused / break) live in a stack that
  cross-fades (220 ms) on ``engine.state_changed``.
* While a session is active, a centred banner strip above the stack shows the away banner
  ("You were away 1h 52m — Resume · Finish (save 23 min) · Abandon") and the firm nudge's paused
  copy ("Still paused on …"). The idle banners live inside the idle view.
* Single owner of ``engine.session_finished`` → ``session_complete.present_session_complete`` and
  of ``break_finished(True)`` (sound, notification when hidden, prefilled task field).
* ``settings.after_start == "mini"``: 900 ms after a start the window hides to the mini timer.
* Space toggles pause / "C" focuses the capture field — only when no text input, spinner or
  button has focus (handled here, not as QShortcuts).

Shell hooks (kept stable): ``on_shown()``, ``start_from_input()``, ``focus_task_input()``,
``set_nudge_banner(visible)``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from madhura.core.timefmt import fmt_duration
from madhura.ui import animations, dialogs
from madhura.ui.overlays import session_complete
from madhura.ui.pages.focus.active_view import ActiveView
from madhura.ui.pages.focus.idle_view import IdleView
from madhura.ui.widgets.banner import InlineBanner
from madhura.ui.widgets.chips import ChipGroup, DayChips, SegmentedControl
from madhura.ui.widgets.inputs import Stepper
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.sheet import ConfirmSheet

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["FocusPage", "AFTER_START_HIDE_MS", "CROSS_FADE_MS"]

log = logging.getLogger(__name__)

AFTER_START_HIDE_MS = 900
CROSS_FADE_MS = 220
ACTIVE_COLUMN_WIDTH = 560

#: Focus owners that keep Space / "C" for themselves (text inputs, spinners, buttons, chips).
_KEY_OWNERS = (
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QAbstractSpinBox,
    QAbstractButton,
    QAbstractSlider,
    QComboBox,
    Stepper,
    ChipGroup,
    SegmentedControl,
    DayChips,
)

IDLE, ACTIVE = 0, 1


def _quote(text: str, limit: int = 48) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f"‘{text}’"


class FocusPage(QWidget):
    """The Focus page (main-window page key "focus")."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._nudge_visible = False
        self._away_seconds: int | None = None
        self._focus_after_idle = False
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Banner strip shown above the active view (away / paused nudge).
        self.active_banners = QWidget(self)
        strip = QHBoxLayout(self.active_banners)
        strip.setContentsMargins(32, 24, 32, 0)
        strip.addStretch(1)
        column = QWidget(self.active_banners)
        column.setMaximumWidth(ACTIVE_COLUMN_WIDTH)
        column_lay = QVBoxLayout(column)
        column_lay.setContentsMargins(0, 0, 0, 0)
        column_lay.setSpacing(12)
        self.away_banner = InlineBanner("info", "", icon="clock", dismissible=True, parent=column)
        self.away_banner.dismissed.connect(self._on_away_dismissed)
        self.paused_nudge_banner = InlineBanner("accent", "", icon="bell", parent=column)
        column_lay.addWidget(self.away_banner)
        column_lay.addWidget(self.paused_nudge_banner)
        strip.addWidget(column, 1000)
        strip.addStretch(1)
        self.away_banner.hide()
        self.paused_nudge_banner.hide()
        self.active_banners.hide()
        lay.addWidget(self.active_banners)

        self._stack = QStackedWidget(self)
        self.idle_view = IdleView(ctx, self._stack)
        self.active_view = ActiveView(ctx, self._stack)
        self._stack.addWidget(self.idle_view)
        self._stack.addWidget(self.active_view)
        lay.addWidget(self._stack, 1)

        self._mini_timer = QTimer(self)
        self._mini_timer.setSingleShot(True)
        self._mini_timer.setInterval(AFTER_START_HIDE_MS)
        self._mini_timer.timeout.connect(self._hide_to_mini)

        engine = ctx.engine
        engine.state_changed.connect(self._on_state)
        engine.session_started.connect(self._on_session_started)
        engine.session_finished.connect(self._on_session_finished)
        engine.break_finished.connect(self._on_break_finished)
        engine.away_detected.connect(self._on_away_detected)
        ctx.events.main_visibility_changed.connect(self._on_main_visibility)
        self._stack.setCurrentIndex(self._index_for_state())
        self._refresh_active_banners()

    # ================================================================== state routing

    @property
    def stack(self) -> QStackedWidget:
        """The idle/active stack."""
        return self._stack

    def is_idle_shown(self) -> bool:
        """True while the planning view is the current page of the stack."""
        return self._stack.currentIndex() == IDLE

    def _index_for_state(self) -> int:
        return IDLE if str(self._ctx.engine.state) == "idle" else ACTIVE

    def _on_state(self, state: str) -> None:
        state = str(state)
        if state in ("focus", "break") and self._nudge_visible:
            # Nudges are only eligible while idle (or long-paused); drop a stale banner.
            self.set_nudge_banner(False)
        if state != "paused":
            self._away_seconds = None
        index = self._index_for_state()
        previous = self._stack.currentIndex()
        if index != previous:
            self._switch_to(index)
        if index == IDLE and previous != IDLE:
            self.idle_view.on_returned_to_idle()
            self._apply_deferred_focus()
        self._refresh_active_banners()

    def _switch_to(self, index: int) -> None:
        old = self._stack.currentWidget()
        fw = QApplication.focusWidget()
        if fw is not None and old is not None and (fw is old or old.isAncestorOf(fw)):
            # Never let focus fall onto the next tab stop of the new view (a "Finish" button
            # under Space would end the session); park it on the page.
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        if index == ACTIVE:
            self.idle_view.plan_card.close_popovers()
        animations.cross_fade(self._stack, index, CROSS_FADE_MS)

    def _apply_deferred_focus(self) -> None:
        if not self._focus_after_idle:
            return
        self._focus_after_idle = False
        window = self._ctx.window
        if window is not None and window.is_main_active():
            window.focus_task_input()

    def _on_session_started(self, _session_id: int) -> None:
        self._mini_timer.stop()
        window = self._ctx.window
        if window is None or self._ctx.settings.get("after_start") != "mini":
            return
        if window.is_main_visible():
            self._mini_timer.start()

    def _hide_to_mini(self) -> None:
        window = self._ctx.window
        if window is None or str(self._ctx.engine.state) != "focus":
            return
        if not window.is_main_visible() or window.has_sheet():
            return
        window.hide_main()

    def _on_session_finished(self, session_id: int, outcome: str, restored: bool) -> None:
        self._mini_timer.stop()
        try:
            session_complete.present_session_complete(self._ctx, int(session_id), str(outcome), bool(restored))
        except Exception:  # noqa: BLE001 - the summary must never break the engine's emission chain
            log.exception("present_session_complete failed")

    def _on_break_finished(self, natural: bool) -> None:
        if not natural:
            return
        ctx = self._ctx
        ctx.sound.play("break_over")
        window = ctx.window
        if window is None or not window.is_main_visible():
            ctx.notifier.notify("Break's over", "Plan your next block.")
        self._focus_after_idle = window is not None and window.is_main_active()
        # state_changed('idle') follows and prefills the plan card; this covers re-entrant paths.
        QTimer.singleShot(0, self, self._apply_deferred_focus_if_idle)

    def _apply_deferred_focus_if_idle(self) -> None:
        if self.is_idle_shown():
            self._apply_deferred_focus()

    def _on_main_visibility(self, visible: bool) -> None:
        if not visible:
            return
        try:
            session_complete.show_pending_complete(self._ctx)
        except Exception:  # noqa: BLE001 - a pending summary must never block showing the window
            log.exception("show_pending_complete failed")

    # ================================================================== away + paused nudge banners

    def _on_away_detected(self, seconds: int) -> None:
        self._away_seconds = int(seconds)
        self._refresh_active_banners()

    def _on_away_dismissed(self) -> None:
        self._away_seconds = None
        self._refresh_active_banners()

    def away_banner_visible(self) -> bool:
        """True while the away banner is shown."""
        return self.away_banner.isVisibleTo(self) and self.active_banners.isVisibleTo(self)

    def _refresh_active_banners(self) -> None:
        engine = self._ctx.engine
        paused = str(engine.state) == "paused"
        if self._away_seconds is not None and paused:
            focused = float(engine.focused_seconds)
            finish = f"Finish (save {int(focused // 60)} min)" if focused >= 60 else "Finish"
            self.away_banner.set_text(f"You were away {fmt_duration(self._away_seconds)}")
            self.away_banner.set_actions([("Resume", self._resume), (finish, self._finish), ("Abandon", self.confirm_abandon)])
            self.away_banner.show()
        else:
            self.away_banner.hide()
        if self._nudge_visible and paused:
            self.paused_nudge_banner.set_text(f"Still paused on {_quote(engine.task)}")
            self.paused_nudge_banner.set_actions(
                [("Resume", self._resume), ("Finish", self._finish), ("Snooze ▾", self._open_paused_snooze)]
            )
            self.paused_nudge_banner.show()
        else:
            self.paused_nudge_banner.hide()
        show = not self.is_idle_target() and (
            self.away_banner.isVisibleTo(self.active_banners) or self.paused_nudge_banner.isVisibleTo(self.active_banners)
        )
        self.active_banners.setVisible(show)

    def is_idle_target(self) -> bool:
        """True when the engine is idle (the stack shows / is moving to the idle view)."""
        return self._index_for_state() == IDLE

    def _resume(self) -> None:
        self._away_seconds = None
        self._ctx.engine.resume()
        self._refresh_active_banners()

    def _finish(self) -> None:
        self._away_seconds = None
        self._ctx.engine.finish()

    def confirm_abandon(self) -> None:
        """Ask (ConfirmSheet) before abandoning the session from the away banner."""
        engine = self._ctx.engine
        if not engine.is_active:
            return
        title = "Abandon this session?"
        text = f"{_quote(engine.task)} won't count toward today."
        window = self._ctx.window
        if window is None:
            if dialogs.confirm(self, title, text, "Abandon", danger=True):
                engine.abandon()
            return
        sheet = ConfirmSheet(title, text, "Abandon", danger=True)
        sheet.accepted.connect(self._abandon_confirmed)
        window.show_sheet(sheet)

    def _abandon_confirmed(self) -> None:
        self._away_seconds = None
        self._ctx.engine.abandon()

    def _open_paused_snooze(self) -> None:
        menu = Menu(self)
        for label, minutes in (("15 min", 15), ("30 min", 30), ("1 hour", 60)):
            menu.add_action(label, lambda m=minutes: self._snooze(m))
        self._paused_menu = menu
        buttons = self.paused_nudge_banner.action_buttons()
        anchor = buttons[-1] if buttons else self.paused_nudge_banner
        menu.popup(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _snooze(self, minutes: int) -> None:
        self.set_nudge_banner(False)
        nudges = self._ctx.nudges
        if nudges is not None:
            nudges.snooze(minutes)

    # ================================================================== shell API

    def on_shown(self) -> None:
        """Called by the main window whenever this page becomes visible."""
        if self.is_idle_shown():
            self.idle_view.on_shown()
        hook = getattr(self.active_view, "on_shown", None)
        if callable(hook) and not self.is_idle_shown():
            hook()

    def start_from_input(self) -> int | None:
        """Ctrl+Enter: start from the plan card (idle) or the break view's next-block field."""
        state = str(self._ctx.engine.state)
        if state == "idle":
            return self.idle_view.plan_card.start()
        if state == "break":
            hook = getattr(self.active_view, "start_from_input", None)
            if callable(hook):
                return hook()  # type: ignore[no-any-return]
        return None

    def focus_task_input(self) -> None:
        """Ctrl+N / "Plan now": focus the task field (idle) or the break view's next-block field."""
        state = str(self._ctx.engine.state)
        if state == "idle":
            if not self.is_idle_shown():
                self._stack.setCurrentIndex(IDLE)
            self.idle_view.plan_card.focus_task()
        elif state == "break":
            hook = getattr(self.active_view, "focus_task_input", None)
            if callable(hook):
                hook()

    def set_nudge_banner(self, visible: bool) -> None:
        """Show/hide the firm-nudge banner (idle copy, or "Still paused on …" while paused)."""
        self._nudge_visible = bool(visible)
        self.idle_view.set_nudge_banner(self._nudge_visible and str(self._ctx.engine.state) == "idle")
        self._refresh_active_banners()

    def nudge_banner_visible(self) -> bool:
        """True while the firm-nudge banner is requested."""
        return self._nudge_visible

    # ================================================================== keyboard

    @staticmethod
    def _key_owner_has_focus() -> bool:
        fw = QApplication.focusWidget()
        return fw is not None and isinstance(fw, _KEY_OWNERS)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if mods == Qt.KeyboardModifier.NoModifier and not event.isAutoRepeat():
            key = event.key()
            engine = self._ctx.engine
            if key == Qt.Key.Key_Space and engine.is_active and not self._key_owner_has_focus():
                engine.toggle_pause()
                event.accept()
                return
            if key == Qt.Key.Key_C and engine.is_active and not self._key_owner_has_focus():
                hook = getattr(self.active_view, "focus_capture", None)
                if callable(hook):
                    hook()
                    event.accept()
                    return
        super().keyPressEvent(event)

    # ================================================================== misc

    def flush_pending(self) -> None:
        """Commit pending undo-deletes (quit path also flushes through the toast host)."""
        self.idle_view.flush_pending()
