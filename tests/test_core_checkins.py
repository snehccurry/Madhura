from __future__ import annotations

import pytest

from madhura.core.checkins import CheckInPlanner

BASE = 7 * 60


def test_first_checkin_after_one_full_interval() -> None:
    planner = CheckInPlanner(7)
    assert planner.next_due == BASE
    assert planner.current_interval == BASE
    assert not planner.is_due(BASE - 1, 3000)
    assert planner.is_due(BASE, 3000)
    assert planner.seconds_until_next(100) == BASE - 100
    assert planner.seconds_until_next(BASE + 50) == 0


def test_no_checkin_with_less_than_60s_remaining() -> None:
    planner = CheckInPlanner(7)
    assert not planner.is_due(BASE + 10, 59.9)
    assert planner.is_due(BASE + 10, 60)


def test_reset_starts_from_focused_seconds() -> None:
    planner = CheckInPlanner(5)
    planner.fired(300)
    planner.record("focused")
    planner.reset(120)
    assert planner.streak == 0
    assert planner.next_due == 120 + 300


def test_fired_schedules_next() -> None:
    planner = CheckInPlanner(7)
    planner.fired(BASE + 3)
    assert planner.next_due == BASE + 3 + BASE


def test_adaptive_spacing() -> None:
    planner = CheckInPlanner(10, adaptive=True)
    base = 600.0
    focused = 0.0
    intervals = []
    for _ in range(8):
        focused = planner.next_due
        planner.fired(focused)
        planner.record("focused")
        intervals.append(planner.next_due - focused)
    # streak after each answer: 1,2,3,4,5,6,7,8
    assert intervals == [base, base, base * 1.5, base * 1.5, base * 1.5, base * 2, base * 2, base * 2]
    assert planner.streak == 8
    assert planner.current_interval == base * 2


@pytest.mark.parametrize("response", ["drifted", "missed"])
def test_drift_or_miss_resets_to_base(response: str) -> None:
    planner = CheckInPlanner(10)
    for _ in range(4):
        planner.fired(planner.next_due)
        planner.record("focused")
    assert planner.current_interval == 900
    fired_at = planner.next_due
    planner.fired(fired_at)
    planner.record(response)
    assert planner.streak == 0
    assert planner.current_interval == 600
    assert planner.next_due == fired_at + 600


def test_non_adaptive_keeps_base() -> None:
    planner = CheckInPlanner(7, adaptive=False)
    for _ in range(10):
        planner.fired(planner.next_due)
        planner.record("focused")
    assert planner.current_interval == BASE
    assert not planner.adaptive


def test_configure_mid_session() -> None:
    planner = CheckInPlanner(7)
    planner.fired(420)
    planner.configure(15, adaptive=True)
    assert planner.base_interval == 900
    assert planner.next_due == 420 + 900
    fresh = CheckInPlanner(7)
    fresh.configure(10, adaptive=False)
    assert fresh.next_due == 600


def test_enum_response_accepted() -> None:
    from madhura.core.models import CheckInResponse

    planner = CheckInPlanner(7)
    planner.record(CheckInResponse.FOCUSED)
    assert planner.streak == 1
    planner.record(CheckInResponse.MISSED)
    assert planner.streak == 0
