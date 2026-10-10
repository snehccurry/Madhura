"""Focus page building blocks: the idle (planning) view [P1] and the active session view [P2].

``idle_view`` — PlanCard, UpNextCard, TodayCard, ParkingLotCard, IdleView (spec §1.2).
``active_view`` — ActiveView: ring, controls, check-in strip, capture, break view (spec §1.3).
The container that switches between them is :class:`madhura.ui.pages.focus_page.FocusPage`.
"""

from __future__ import annotations
