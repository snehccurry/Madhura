"""Madhura widget kit — import everything from here::

    from madhura.ui.widgets import Button, Card, ChipGroup, TextField, ...

Conventions shared by every widget:

* Colours are read from ``theme.current().palette`` at paint time, so widgets follow theme
  switches without rebuilding.
* Programmatic setters (``set_value``, ``set_mask``, ``set_time_str`` …) never emit the
  change signal; user interaction does. (Qt-native ``toggled`` on Toggle/Checkbox is the
  exception — use ``clicked`` for user-only changes.)
* Custom focusable widgets show a 2 px accent focus ring (offset 2 px) only for keyboard focus.
"""

from __future__ import annotations

from madhura.ui.widgets.banner import BANNER_TONES, InlineBanner
from madhura.ui.widgets.buttons import SIZES as BUTTON_SIZES
from madhura.ui.widgets.buttons import VARIANTS as BUTTON_VARIANTS
from madhura.ui.widgets.buttons import Button, IconButton, LinkButton
from madhura.ui.widgets.card import Card
from madhura.ui.widgets.checkbox import Checkbox
from madhura.ui.widgets.chips import Chip, ChipGroup, DayChips, SegmentedControl, normalize_options
from madhura.ui.widgets.empty_state import EmptyState, IconMedallion
from madhura.ui.widgets.inputs import (
    FieldFrame,
    Slider,
    Stepper,
    TextArea,
    TextField,
    TimeField,
    WheelGuard,
    install_wheel_guard,
    style_completer,
)
from madhura.ui.widgets.labels import (
    BADGE_TONES,
    Badge,
    Caption,
    ElidedLabel,
    Heading,
    Kbd,
    Label,
    Micro,
    SectionHeader,
)
from madhura.ui.widgets.list_row import ListRow
from madhura.ui.widgets.menu import Menu, is_windows_11, round_window_corners
from madhura.ui.widgets.page import PageHeader, PageScroll
from madhura.ui.widgets.popover import Popover
from madhura.ui.widgets.popup import PRIORITIES, FloatingPanel, PopupStack, compute_layout, paint_shadow
from madhura.ui.widgets.progress_ring import ProgressRing
from madhura.ui.widgets.select import Select
from madhura.ui.widgets.settings_row import Divider, SettingsGroup, SettingsRow
from madhura.ui.widgets.sheet import ConfirmSheet, SheetContent, SheetHost
from madhura.ui.widgets.toast import TOAST_KINDS, ToastHost
from madhura.ui.widgets.toggle import Toggle

__all__ = [
    "BADGE_TONES",
    "BANNER_TONES",
    "BUTTON_SIZES",
    "BUTTON_VARIANTS",
    "PRIORITIES",
    "TOAST_KINDS",
    "Badge",
    "Button",
    "Caption",
    "Card",
    "Checkbox",
    "Chip",
    "ChipGroup",
    "ConfirmSheet",
    "DayChips",
    "Divider",
    "ElidedLabel",
    "EmptyState",
    "FieldFrame",
    "FloatingPanel",
    "Heading",
    "IconButton",
    "IconMedallion",
    "InlineBanner",
    "Kbd",
    "Label",
    "LinkButton",
    "ListRow",
    "Menu",
    "Micro",
    "PageHeader",
    "PageScroll",
    "Popover",
    "PopupStack",
    "ProgressRing",
    "SectionHeader",
    "SegmentedControl",
    "Select",
    "SettingsGroup",
    "SettingsRow",
    "SheetContent",
    "SheetHost",
    "Slider",
    "Stepper",
    "TextArea",
    "TextField",
    "TimeField",
    "ToastHost",
    "Toggle",
    "WheelGuard",
    "compute_layout",
    "install_wheel_guard",
    "is_windows_11",
    "normalize_options",
    "paint_shadow",
    "round_window_corners",
    "style_completer",
]
