"""STUB — owned by [H]. HabitEditorSheet: create / edit a habit (spec §1.8).

Created by the app shell [F3] with a name field only; the habits agent replaces this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QWidget

from madhura.ui.widgets.buttons import Button
from madhura.ui.widgets.inputs import TextField
from madhura.ui.widgets.sheet import SheetContent

if TYPE_CHECKING:
    from madhura.context import AppContext
    from madhura.core.models import Habit

__all__ = ["HabitEditorSheet"]


class HabitEditorSheet(SheetContent):
    """New / edit habit sheet (440 px). STUB: name only."""

    def __init__(
        self,
        ctx: AppContext,
        habit: Habit | None = None,
        prefill: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._habit = habit
        self.set_title("Edit habit" if habit is not None else "New habit")
        self.name_field = TextField("e.g. Read 10 pages", parent=self)
        name = habit.name if habit is not None else (prefill or {}).get("name", "")
        self.name_field.set_text(str(name or ""))
        self.name_field.returnPressed.connect(self.save)
        self.body.addWidget(self.name_field)
        cancel = Button("Cancel", "ghost", parent=self)
        cancel.clicked.connect(self.close_sheet)
        save = Button("Save", "primary", parent=self)
        save.clicked.connect(self.save)
        self.add_footer_button(cancel)
        self.add_footer_button(save, primary=True)

    def save(self) -> None:
        """Validate, write and close."""
        name = self.name_field.text().strip()
        if not name:
            self.name_field.set_error("Give your habit a name")
            return
        if self._habit is None:
            self._ctx.repo.add_habit(name)
        else:
            self._ctx.repo.update_habit(self._habit.id, name=name)
        self._ctx.events.data_changed.emit("habits")
        self.close_sheet()
