"""Floating panels and sheets that sit above the main window: mini timer pill, check-in / nudge /
away / habit-reminder panels, session-complete and wrap-up sheets, onboarding.

Floating panels are always parentless (see ``ui.widgets.popup``) and kept alive by their
controller service; sheets are shown through ``ctx.window.show_sheet``.
"""

from __future__ import annotations
