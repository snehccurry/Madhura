"""STUB — owned by [S]. SettingsPage: grouped, instant-apply settings (spec §1.10).

Created by the app shell [F3] with Appearance + About only; the settings agent replaces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from madhura import __version__
from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.chips import SegmentedControl
from madhura.ui.widgets.labels import Label
from madhura.ui.widgets.page import PageHeader, PageScroll
from madhura.ui.widgets.settings_row import SettingsGroup
from madhura.ui.widgets.toggle import Toggle

if TYPE_CHECKING:
    from madhura.context import AppContext

__all__ = ["SettingsPage"]


class SettingsPage(PageScroll):
    """Settings page (max width 720). STUB."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(720, parent)
        self._ctx = ctx
        self.add(PageHeader("Settings", parent=self.column))
        self.content_layout.addSpacing(8)

        appearance = SettingsGroup("Appearance", parent=self.column)
        self.theme_control = SegmentedControl(
            [("system", "System"), ("dark", "Dark"), ("light", "Light")], ctx.settings.get("theme"), size="sm"
        )
        self.theme_control.valueChanged.connect(lambda v: ctx.settings.update(theme=v))
        appearance.add("Theme", None, self.theme_control)
        self.motion_toggle = Toggle(ctx.settings.get("reduce_motion"))
        self.motion_toggle.clicked.connect(lambda: ctx.settings.update(reduce_motion=self.motion_toggle.isChecked()))
        appearance.add("Reduce motion", "Skip animations and keep transitions instant.", self.motion_toggle)
        self.add(appearance)

        about = SettingsGroup("About", parent=self.column)
        about.add("Version", None, Label(__version__, "caption"))
        shortcuts = Button("Keyboard shortcuts", "secondary", icon="keyboard")
        shortcuts.clicked.connect(self._show_shortcuts)
        about.add("Keyboard shortcuts", "Every shortcut at a glance.", shortcuts)
        self.add(about)
        ctx.settings.changed.connect(self._on_setting)

    def _on_setting(self, key: str) -> None:
        if key == "theme":
            self.theme_control.set_value(self._ctx.settings.get("theme"))
        elif key == "reduce_motion":
            self.motion_toggle.setChecked(self._ctx.settings.get("reduce_motion"))

    def _show_shortcuts(self) -> None:
        from madhura.ui.shortcuts_sheet import ShortcutsSheet

        if self._ctx.window is not None:
            self._ctx.window.show_sheet(ShortcutsSheet())

    def on_shown(self) -> None:
        """Refresh when shown. STUB: controls track settings live."""
