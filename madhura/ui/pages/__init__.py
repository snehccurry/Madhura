"""Main-window pages: Focus, Habits, Insights, Settings (see ``ui.main_window``).

``PAGES`` lists ``(key, label, icon, shortcut)`` in sidebar order; the page classes live in their
own modules and are imported lazily by the main window.
"""

from __future__ import annotations

__all__ = ["PAGES", "PAGE_KEYS", "page_label"]

#: (key, sidebar label, icon name, shortcut) in sidebar order.
PAGES: tuple[tuple[str, str, str, str], ...] = (
    ("focus", "Focus", "target", "Ctrl+1"),
    ("habits", "Habits", "repeat", "Ctrl+2"),
    ("insights", "Insights", "chart", "Ctrl+3"),
    ("settings", "Settings", "settings", "Ctrl+4"),
)

PAGE_KEYS: tuple[str, ...] = tuple(p[0] for p in PAGES)


def page_label(key: str) -> str:
    """Human label for a page key ("focus" → "Focus")."""
    for k, label, _icon, _sc in PAGES:
        if k == key:
            return label
    raise KeyError(key)
