"""STUB — owned by [P2]. Session end presentation (spec §1.4).

``present_session_complete`` is called by FocusPage for every ``engine.session_finished``. This
stub, created by the app shell [F3], only shows toasts; the focus-active agent replaces it with the
SessionCompleteSheet / CompletionPanel flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from madhura.core.timefmt import fmt_duration
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.labels import Label
from madhura.ui.widgets.popup import FloatingPanel
from madhura.ui.widgets.sheet import SheetContent

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["present_session_complete", "show_pending_complete", "SessionCompleteSheet", "CompletionPanel"]


class SessionCompleteSheet(SheetContent):
    """In-window "Session complete" sheet. STUB: summary line + Done."""

    def __init__(self, ctx: AppContext, session_id: int, outcome: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.session_id = session_id
        session = ctx.repo.get_session(session_id)
        self.set_title("Session complete" if outcome == "completed" else "Session ended")
        if session is not None:
            self.body.addWidget(Label(session.task, "body", self))
        done = Button("Done", "primary", parent=self)
        done.clicked.connect(self.close_sheet)
        self.add_footer_button(done, primary=True)


class CompletionPanel(FloatingPanel):
    """Non-activating "Nice — 60 min on <task>" panel. STUB: empty panel."""

    def __init__(self, ctx: AppContext | None = None, session_id: int | None = None) -> None:
        super().__init__("completion")
        self._ctx = ctx
        self.session_id = session_id


def present_session_complete(ctx: AppContext, session_id: int, outcome: str, restored: bool) -> None:
    """React to ``engine.session_finished``. STUB: toasts only."""
    window = ctx.window
    if window is None:
        return
    session = ctx.repo.get_session(session_id)
    if restored:
        task = session.task if session is not None else "your task"
        window.show_toast(f"Your session on '{task}' finished while Madhura was closed.", "info")
        return
    if outcome == "abandoned":
        window.show_toast("Too short to save" if session is None else "Session discarded", "info")
        return
    if session is None:
        return
    minutes = fmt_duration(session.focused_seconds)
    title = "Session complete" if outcome == "completed" else "Session ended"
    window.show_toast(f"{title} — {minutes} on {session.task}", "success")


def show_pending_complete(ctx: AppContext) -> None:
    """Show the pending SessionCompleteSheet (kv 'pending_complete'). STUB: no-op."""
