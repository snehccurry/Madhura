from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from madhura import APP_ID, APP_NAME, __version__
from madhura.core.models import (
    COUNTED_OUTCOMES,
    CheckInResponse,
    Habit,
    HabitStatus,
    Outcome,
    Session,
    from_date_str,
    from_iso,
    to_date_str,
    to_iso,
)


def test_package_metadata() -> None:
    assert __version__ == "2.0.0"
    assert APP_NAME == "Madhura"
    assert APP_ID == "Madhura.FocusApp"


def test_enums_behave_like_their_values() -> None:
    assert Outcome.COMPLETED == "completed"
    assert f"{Outcome.ENDED_EARLY}" == "ended_early"
    assert str(CheckInResponse.DRIFTED) == "drifted"
    assert {"done": 1}[HabitStatus.DONE] == 1
    assert json.dumps({"o": Outcome.ABANDONED}) == '{"o": "abandoned"}'
    assert Outcome("active") is Outcome.ACTIVE
    assert COUNTED_OUTCOMES == ("completed", "ended_early")
    assert f"{Outcome.COMPLETED:>10}" == " completed"


def test_iso_helpers() -> None:
    dt = datetime(2026, 10, 14, 10, 5, 7, 999999)
    assert to_iso(dt) == "2026-10-14T10:05:07"
    assert from_iso("2026-10-14T10:05:07") == datetime(2026, 10, 14, 10, 5, 7)
    assert from_iso(None) is None
    assert from_iso("") is None
    assert to_date_str(date(2026, 1, 2)) == "2026-01-02"
    assert to_date_str(datetime(2026, 1, 2, 23, 0)) == "2026-01-02"
    assert from_date_str("2026-01-02") == date(2026, 1, 2)
    assert from_date_str(None) is None
    aware = datetime(2026, 10, 14, 10, 0, tzinfo=timezone(timedelta(hours=3)))
    assert "T" in to_iso(aware) and "+" not in to_iso(aware)


def test_model_helpers() -> None:
    s = Session(1, "Write", None, None, datetime(2026, 1, 1), None, 1500, 1500, 0, Outcome.COMPLETED)
    assert s.counted
    assert s.planned_minutes == 25
    s.outcome = Outcome.ABANDONED
    assert not s.counted
    h = Habit(1, "Read", None, None, None, 127, True, False, 60, None, 0, False, datetime(2026, 1, 1))
    assert h.is_auto
    h.focus_minutes = None
    assert not h.is_auto
