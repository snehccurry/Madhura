"""NudgePopup — the gentle planning nudge panel (spec §1.7, §7.3).

Variants (``variant``):

``"next"``     "What's next?" · "Plan your next block to stay intentional." + the service's one-click
               primary action ("Start ‘<first task>’ · 25m" / "Continue ‘<last task>’" / "Plan now"),
               "Snooze ▾" (15 min · 30 min · 1 hour · In a meeting… ▸ 30 min / 1 hour) and
               "Done for today".
``"morning"``  first nudge of the day with a planned task: "Good morning — your first block: <task>".
``"paused"``   "Still paused on <task>" · Resume / Finish / Snooze ▾.
``"wrap_up"``  "Wrap up your day?" → opens the WrapUpSheet.

Lowest stack priority (never shown alongside another panel); hides itself after 30 s without
penalty (hovering pauses that). Created and kept alive by :class:`NudgeService`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QSizePolicy, QWidget

from madhura.core.timefmt import greeting
from madhura.ui.overlays._panel_kit import (
    ClampText,
    Glyph,
    HoverCountdown,
    MenuButton,
    hbox,
    install_focus_guard,
    quote,
    vbox,
)
from madhura.ui.widgets.buttons import Button, IconButton
from madhura.ui.widgets.labels import Caption
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.popup import PRIORITIES, FloatingPanel

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["NudgePopup", "VARIANTS", "AUTO_HIDE_MS", "nudge_copy"]

VARIANTS = ("next", "morning", "paused", "wrap_up")
#: Horizontal space a md Button with a leading icon needs besides its text (2×14 pad + 16 + 8 + slack).
_BUTTON_CHROME = 56
AUTO_HIDE_MS = 30_000
HOVER_RESUME_MS = 8_000

_GLYPHS = {
    "next": ("target", "accent"),
    "morning": ("sunrise", "accent"),
    "paused": ("pause", "neutral"),
    "wrap_up": ("sunset", "accent"),
}


def nudge_copy(variant: str, *, task: str = "", hour_dt: Any = None) -> tuple[str, str]:
    """(title, body) for a nudge variant."""
    if variant == "morning":
        hello = greeting(hour_dt) if hour_dt is not None else "Good morning"
        return f"{hello} — your first block: {task.strip()}", "Start now, or snooze until you're ready."
    if variant == "paused":
        return f"Still paused on {quote(task)}", "Pick up where you left off, or finish and plan what's next."
    if variant == "wrap_up":
        return "Wrap up your day?", "Review today, move what's left and set tomorrow's first block."
    return "What's next?", "Plan your next block to stay intentional."


class NudgePopup(FloatingPanel):
    """Gentle nudge panel (popup stack priority 4). See the module docstring.

    ``service`` is the owning :class:`NudgeService` (None for previews: buttons then only close
    the panel).
    """

    def __init__(
        self, ctx: AppContext | None = None, service: Any = None, variant: str = "next", *args: Any, **kwargs: Any
    ) -> None:
        super().__init__("nudge")
        install_focus_guard()
        self.priority = PRIORITIES["nudge"]
        self._ctx = ctx
        self._service = service
        self.variant = variant if variant in VARIANTS else "next"
        self._menu: Menu | None = None
        self._acted: str | None = None
        self.setAccessibleName("Planning nudge")

        engine = ctx.engine if ctx is not None else None
        task = ""
        if self.variant == "paused" and engine is not None:
            task = engine.task
        elif self.variant == "morning" and service is not None:
            first = service.first_task()
            task = first.title if first is not None else ""
        now = ctx.clock.now() if ctx is not None else None
        title, body = nudge_copy(self.variant, task=task, hour_dt=now)

        page = QWidget()
        self.page = page
        v = vbox(page)
        top = hbox(spacing=12)
        icon, tone = _GLYPHS[self.variant]
        self.glyph = Glyph(icon, 32, page, tone=tone)
        top.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop)
        texts = vbox(spacing=4)
        title_row = hbox(spacing=8)
        self.title = ClampText(title, 15, 600, 2, page)
        title_row.addWidget(self.title, 1)
        self.close_button = IconButton("x", "Dismiss", 24, page, glyph=14)
        self.close_button.clicked.connect(self.dismiss_quietly)
        title_row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        texts.addLayout(title_row)
        self.body = Caption(body, page)
        self.body.setWordWrap(True)
        texts.addWidget(self.body)
        top.addLayout(texts, 1)
        v.addLayout(top)
        v.addSpacing(16)

        self.primary_button = Button("", "primary", size="md", parent=page)
        self.primary_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.secondary_button = Button("", "secondary", size="md", parent=page)
        self.snooze_button = MenuButton("Snooze", "secondary", "md", page)
        self.done_button = Button("Done for today", "ghost", size="md", parent=page)
        first = hbox(spacing=8)
        first.addWidget(self.primary_button, 1)
        v.addLayout(first)
        if self.variant == "wrap_up":
            first.addWidget(self.done_button)
            self.secondary_button.hide()
            self.snooze_button.hide()
        else:
            v.addSpacing(8)
            row = hbox(spacing=8)
            row.addWidget(self.secondary_button)
            row.addWidget(self.snooze_button)
            row.addStretch(1)
            row.addWidget(self.done_button)
            v.addLayout(row)
        self._configure_buttons()
        self.set_content(page, animate=False)

        self.auto_hide = HoverCountdown(self, AUTO_HIDE_MS, self._auto_hidden, min_resume_ms=HOVER_RESUME_MS)
        self.hover_changed.connect(self.auto_hide.set_hover)
        self.closed.connect(self._on_closed)

    # ------------------------------------------------------------------ buttons
    def _configure_buttons(self) -> None:
        variant = self.variant
        if variant != "paused":
            self.secondary_button.hide()
        self.primary_button.clicked.connect(self._on_primary)
        self.snooze_button.clicked.connect(self.open_snooze_menu)
        self.done_button.clicked.connect(self._on_done)
        self.snooze_button.setToolTip("Snooze nudges")
        if variant == "paused":
            self.primary_button.setText("Resume")
            self.primary_button.set_icon_name("play")
            self.secondary_button.setText("Finish")
            self.secondary_button.clicked.connect(self._on_finish)
            self.done_button.hide()
        elif variant == "wrap_up":
            self.primary_button.setText("Wrap up the day")
            self.done_button.setText("Not now")
        else:
            verb, title, suffix = "Plan now", None, ""
            if self._service is not None:
                verb, title, suffix, _cb = self._service.primary_parts()
            if variant == "morning" and verb == "Start":
                title = None
                verb = "Start now"
            self.primary_button.setText(self._fit_label(verb, title, suffix))
            self.primary_button.set_icon_name("play" if verb.startswith(("Start", "Continue")) else "target")
            full = verb if title is None else f"{verb} ‘{title}’"
            self.primary_button.setToolTip(f"{full} · {suffix}" if suffix else full)
            return
        self.primary_button.setToolTip(self.primary_button.text())

    def _fit_label(self, verb: str, title: str | None, suffix: str) -> str:
        """``Start ‘<title>’ · 25m`` with the title elided so the label fits the button."""
        tail = f" · {suffix}" if suffix else ""
        if not title:
            return f"{verb}{tail}"
        fm = QFontMetrics(self.primary_button.font())
        avail = self.card_width() - 2 * self.padding - _BUTTON_CHROME - fm.horizontalAdvance(f"{verb} ‘’{tail}")
        shown = fm.elidedText(title.strip(), Qt.TextElideMode.ElideRight, max(40, avail))
        return f"{verb} ‘{shown}’{tail}"

    def primary_text(self) -> str:
        """Label on the primary button."""
        return self.primary_button.text()

    def acted(self) -> str | None:
        """What the user did ("primary", "finish", "snooze", "meeting", "done", "dismiss", "timeout")."""
        return self._acted

    # ------------------------------------------------------------------ presenting
    def present(self, priority: int | None = None) -> None:
        """Push to the popup stack (lowest priority) and start the 30 s auto-hide."""
        super().present(PRIORITIES["nudge"] if priority is None else priority)
        self.auto_hide.start(AUTO_HIDE_MS)

    def dismiss_quietly(self) -> None:
        """Close without any action (✕ button)."""
        self._acted = self._acted or "dismiss"
        self.dismiss()

    def _auto_hidden(self) -> None:
        self._acted = self._acted or "timeout"
        self.dismiss()

    # ------------------------------------------------------------------ actions
    def _act(self, name: str) -> bool:
        if self._acted is not None:
            return False
        self._acted = name
        self.auto_hide.stop()
        return True

    def _on_primary(self) -> None:
        if not self._act("primary"):
            return
        self.dismiss()
        service = self._service
        if service is None:
            return
        if self.variant == "wrap_up":
            service.open_wrap_up()
        elif self.variant == "paused":
            service.resume_session()
        else:
            _label, callback = service.primary_action()
            callback()

    def _on_finish(self) -> None:
        if not self._act("finish"):
            return
        self.dismiss()
        if self._ctx is not None:
            self._ctx.engine.finish()

    def _on_done(self) -> None:
        if self.variant == "wrap_up":
            self.dismiss_quietly()
            return
        if not self._act("done"):
            return
        self.dismiss()
        if self._service is not None:
            self._service.done_for_today()

    def snooze(self, minutes: int) -> None:
        """Snooze nudges for *minutes* (menu item)."""
        if not self._act("snooze"):
            return
        self.dismiss()
        if self._service is not None:
            self._service.snooze(int(minutes))

    def meeting(self, minutes: int) -> None:
        """"In a meeting…": start a Meeting block of *minutes*."""
        if not self._act("meeting"):
            return
        self.dismiss()
        if self._service is not None:
            self._service.start_meeting(int(minutes))

    def snooze_menu(self) -> Menu:
        """Build the Snooze ▾ menu (15 min · 30 min · 1 hour · In a meeting… ▸ 30 min / 1 hour)."""
        menu = Menu(self)
        menu.add_action("15 min", lambda: self.snooze(15), icon="bell_off")
        menu.add_action("30 min", lambda: self.snooze(30))
        menu.add_action("1 hour", lambda: self.snooze(60))
        if self.variant != "paused":
            menu.add_separator()
            sub = menu.add_menu("In a meeting…", icon="calendar")
            sub.add_action("30 min", lambda: self.meeting(30))
            sub.add_action("1 hour", lambda: self.meeting(60))
        return menu

    def open_snooze_menu(self) -> None:
        """Show the Snooze menu under its button (pauses the auto-hide while open)."""
        menu = self.snooze_menu()
        self._menu = menu
        self.auto_hide.set_hover(True)
        menu.aboutToHide.connect(lambda: self.auto_hide.set_hover(False))
        menu.popup_below(self.snooze_button)

    def _on_closed(self) -> None:
        self.auto_hide.stop()
        menu = self._menu
        self._menu = None
        if menu is not None:
            try:
                menu.close()
            except RuntimeError:
                pass
