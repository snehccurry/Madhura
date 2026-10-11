"""Pure habit logic: core/habits.py (spec §1.8)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from madhura.core import habits as hc
from madhura.core.models import Habit, HabitLog, HabitStatus, Outcome, Session

WED = date(2026, 10, 14)  # Wednesday
MON = date(2026, 10, 12)


def habit(
    hid: int = 1,
    *,
    time: str | None = None,
    days: int = 127,
    reminder: bool = True,
    urgent: bool = False,
    focus_minutes: int | None = None,
    project: str | None = None,
    position: int = 0,
    archived: bool = False,
    created: datetime | None = datetime(2026, 9, 1, 9, 0),
    name: str = "Read",
) -> Habit:
    return Habit(
        id=hid,
        name=name,
        emoji=None,
        cue=None,
        time=time,
        days=days,
        reminder=reminder,
        urgent=urgent,
        focus_minutes=focus_minutes,
        project=project,
        position=position,
        archived=archived,
        created_at=created,  # type: ignore[arg-type]
    )


def logs(hid: int, entries: dict[date, str]) -> list[HabitLog]:
    return [HabitLog(i, hid, d, HabitStatus(s), datetime.combine(d, datetime.min.time())) for i, (d, s) in enumerate(entries.items())]


def days_back(n: int, today: date = WED) -> date:
    return today - timedelta(days=n)


# --------------------------------------------------------------------------- basics


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("08:00", (8, 0)),
        ("23:59", (23, 59)),
        ("7:05", (7, 5)),
        (None, None),
        ("", None),
        ("  ", None),
        ("bad", None),
        ("25:00", None),
        ("12:61", None),
    ],
)
def test_habit_time_parsing(raw: str | None, expected: tuple[int, int] | None) -> None:
    t = hc.habit_time(habit(time=raw))
    assert (None if t is None else (t.hour, t.minute)) == expected


def test_is_scheduled_masks() -> None:
    sat = date(2026, 10, 17)
    assert hc.is_scheduled(habit(days=127), sat)
    assert hc.is_scheduled(habit(days=31), WED) and not hc.is_scheduled(habit(days=31), sat)
    assert hc.is_scheduled(habit(days=96), sat) and not hc.is_scheduled(habit(days=96), WED)
    assert not hc.is_scheduled(habit(days=0), WED)
    assert hc.is_scheduled(habit(days=1), MON) and not hc.is_scheduled(habit(days=1), WED)
    # bits above Sunday are ignored
    assert not hc.is_scheduled(habit(days=128), MON)


def test_created_date_and_logs_by_date_filters_other_habits() -> None:
    h = habit(created=datetime(2026, 10, 1, 23, 30))
    assert hc.created_date(h) == date(2026, 10, 1)
    assert hc.created_date(habit(created=None)) is None
    mixed = logs(1, {WED: "done"}) + logs(2, {days_back(1): "skipped"})
    assert hc.logs_by_date(h, mixed) == {WED: "done"}


def test_created_date_is_the_logical_day() -> None:
    # 01:30 on Thu 15 Oct with a 04:00 day start is still logical Wednesday
    h = habit(created=datetime(2026, 10, 15, 1, 30))
    assert hc.created_date(h, day_start_hour=4) == WED
    assert hc.created_date(h) == date(2026, 10, 15)  # midnight day start: the calendar date
    assert hc.created_date(habit(created=datetime(2026, 10, 15, 4, 0)), day_start_hour=4) == date(2026, 10, 15)


def test_habit_created_after_midnight_before_day_start_belongs_to_that_logical_day() -> None:
    weekdays = habit(days=31, created=datetime(2026, 10, 15, 1, 30))  # logical Wed with day start 4
    # not done yet: today is pending, not "unscheduled" as if the habit did not exist yet
    assert hc.day_state(weekdays, {}, WED, WED, day_start_hour=4) == "pending"
    assert dict(hc.last_7(weekdays, [], WED, day_start_hour=4))[WED] == "pending"
    assert dict(hc.last_7(weekdays, [], WED, day_start_hour=4))[days_back(1)] == "unscheduled"  # before creation
    assert hc.habit_streak(weekdays, logs(1, {WED: "done"}), WED, day_start_hour=4) == (1, 1)
    # the next morning: Wednesday counts as a missed day of its own, Tuesday still doesn't
    thu = WED + timedelta(days=1)
    assert hc.day_state(weekdays, {}, WED, thu, day_start_hour=4) == "missed"
    assert hc.missed_yesterday(weekdays, [], thu, day_start_hour=4) is True
    assert hc.missed_yesterday(weekdays, [], thu) is False  # calendar creation date = Thursday


# --------------------------------------------------------------------------- last 7 days


def test_last_7_states_oldest_first() -> None:
    h = habit()
    entries = {days_back(6): "done", days_back(5): "skipped", days_back(3): "done", WED: "done"}
    out = hc.last_7(h, logs(1, entries), WED)
    assert [d for d, _ in out] == [days_back(i) for i in range(6, -1, -1)]
    assert [s for _, s in out] == ["done", "skipped", "missed", "done", "missed", "missed", "done"]
    assert set(s for _, s in out) <= set(hc.STATES)


def test_last_7_pending_today_unscheduled_and_before_creation() -> None:
    weekdays = habit(days=31, created=datetime(2026, 10, 10, 8, 0))  # created Saturday
    out = dict(hc.last_7(weekdays, [], WED))
    assert out[WED] == "pending"
    assert out[date(2026, 10, 9)] == "unscheduled"  # Friday before creation
    assert out[date(2026, 10, 10)] == "unscheduled" and out[date(2026, 10, 11)] == "unscheduled"  # weekend
    assert out[MON] == "missed" and out[date(2026, 10, 13)] == "missed"


def test_last_7_logs_win_over_schedule() -> None:
    weekdays = habit(days=31)
    sun = date(2026, 10, 11)
    out = dict(hc.last_7(weekdays, logs(1, {sun: "done"}), WED))
    assert out[sun] == "done"


def test_day_state_future_is_unscheduled() -> None:
    h = habit()
    assert hc.day_state(h, {}, WED + timedelta(days=1), WED) == "unscheduled"


# --------------------------------------------------------------------------- streaks


def test_streak_counts_consecutive_done_including_today() -> None:
    h = habit()
    entries = {days_back(i): "done" for i in range(0, 5)}
    assert hc.habit_streak(h, logs(1, entries), WED) == (5, 5)


def test_streak_today_pending_does_not_break() -> None:
    h = habit()
    entries = {days_back(i): "done" for i in range(1, 4)}
    assert hc.habit_streak(h, logs(1, entries), WED) == (3, 3)


def test_streak_missed_day_breaks_and_best_is_kept() -> None:
    h = habit()
    entries = {days_back(i): "done" for i in (1, 2, 4, 5, 6, 7, 8)}
    current, best = hc.habit_streak(h, logs(1, entries), WED)
    assert current == 2 and best == 5


def test_streak_skipped_is_neutral() -> None:
    h = habit()
    entries = {days_back(1): "done", days_back(2): "skipped", days_back(3): "done", days_back(4): "done"}
    assert hc.habit_streak(h, logs(1, entries), WED) == (3, 3)
    # skipped today does not break either
    entries[WED] = "skipped"
    assert hc.habit_streak(h, logs(1, entries), WED)[0] == 3


def test_streak_ignores_unscheduled_days_even_when_done() -> None:
    weekdays = habit(days=31)
    # Thu, Fri done; weekend unscheduled; Mon, Tue done → 4 (Saturday's extra 'done' doesn't count)
    entries = {date(2026, 10, 8): "done", date(2026, 10, 9): "done", date(2026, 10, 10): "done", MON: "done", date(2026, 10, 13): "done"}
    assert hc.habit_streak(weekdays, logs(1, entries), WED) == (4, 4)


def test_streak_ignores_days_before_creation() -> None:
    h = habit(created=datetime(2026, 10, 12, 18, 0))
    entries = {MON: "done", date(2026, 10, 13): "done"}
    assert hc.habit_streak(h, logs(1, entries), WED) == (2, 2)
    assert hc.habit_streak(habit(created=datetime(2026, 10, 14, 9, 0)), [], WED) == (0, 0)
    assert hc.habit_streak(habit(created=datetime(2026, 10, 14, 9, 0)), logs(1, {WED: "done"}), WED) == (1, 1)


def test_streak_counts_logs_older_than_creation_date() -> None:
    # e.g. created just after midnight but logged for the previous logical day
    h = habit(created=datetime(2026, 10, 14, 1, 0))
    assert hc.habit_streak(h, logs(1, {date(2026, 10, 13): "done", WED: "done"}), WED) == (2, 2)


def test_streak_empty_and_never_scheduled() -> None:
    assert hc.habit_streak(habit(), [], WED) == (0, 0)
    assert hc.habit_streak(habit(days=0), logs(1, {WED: "done"}), WED) == (0, 0)


def test_streak_clock_set_back_before_creation() -> None:
    h = habit(created=datetime(2026, 10, 20, 9, 0))
    assert hc.habit_streak(h, [], WED) == (0, 0)


# --------------------------------------------------------------------------- never miss twice


def test_missed_yesterday_daily() -> None:
    h = habit()
    assert hc.missed_yesterday(h, [], WED) is True
    assert hc.missed_yesterday(h, logs(1, {days_back(1): "done"}), WED) is False
    assert hc.missed_yesterday(h, logs(1, {days_back(1): "skipped"}), WED) is False
    assert hc.missed_yesterday(h, logs(1, {WED: "done"}), WED) is False  # done today
    assert hc.missed_yesterday(h, logs(1, {WED: "skipped"}), WED) is False


def test_missed_yesterday_respects_creation_and_schedule() -> None:
    assert hc.missed_yesterday(habit(created=datetime(2026, 10, 14, 7, 0)), [], WED) is False
    assert hc.missed_yesterday(habit(days=96), [], WED) is False  # not scheduled today


def test_missed_previous_scheduled_day_for_mwf_habit() -> None:
    mwf = habit(days=0b0010101)
    assert hc.previous_scheduled_day(mwf, WED) == MON
    assert hc.missed_yesterday(mwf, [], WED) is True
    assert hc.missed_yesterday(mwf, logs(1, {MON: "done"}), WED) is False
    assert hc.previous_scheduled_day(habit(days=0), WED) is None


# --------------------------------------------------------------------------- due / lateness


def test_due_at_and_day_start_hour() -> None:
    assert hc.due_at(habit(time="08:30"), WED) == datetime(2026, 10, 14, 8, 30)
    assert hc.due_at(habit(), WED) is None
    late_night = habit(time="01:00")
    assert hc.due_at(late_night, WED, 4) == datetime(2026, 10, 15, 1, 0)
    assert hc.due_at(late_night, WED, 0) == datetime(2026, 10, 14, 1, 0)


def test_minutes_late() -> None:
    h = habit(time="08:00")
    assert hc.minutes_late(h, datetime(2026, 10, 14, 7, 59)) == 0
    assert hc.minutes_late(h, datetime(2026, 10, 14, 8, 0)) == 0
    assert hc.minutes_late(h, datetime(2026, 10, 14, 8, 15, 40)) == 15
    assert hc.minutes_late(h, datetime(2026, 10, 14, 10, 1)) == 121
    assert hc.minutes_late(habit(), datetime(2026, 10, 14, 10, 0)) == 0
    # 01:00 habit, day starts at 04:00: at 01:30 (calendar Thu) it belongs to logical Wed
    assert hc.minutes_late(habit(time="01:00"), datetime(2026, 10, 15, 1, 30), day_start_hour=4) == 30


@pytest.mark.parametrize(
    ("minutes", "label"),
    [
        (-5, "Due now"),
        (0, "Due now"),
        (1, "Due now"),
        (2, "2 min late"),
        (15, "15 min late"),
        (59, "59 min late"),
        (60, "1 hr late"),
        (125, "2 hr late"),
    ],
)
def test_lateness_label(minutes: int, label: str) -> None:
    assert hc.lateness_label(minutes) == label


def test_due_status_happy_path_and_labels() -> None:
    h = habit(time="08:00")
    assert hc.due_status(h, datetime(2026, 10, 14, 7, 59)) is None
    assert hc.due_status(h, datetime(2026, 10, 14, 8, 0)) == "Due now"
    assert hc.due_status(h, datetime(2026, 10, 14, 8, 15)) == "15 min late"
    assert hc.due_status(h, datetime(2026, 10, 14, 10, 30)) == "2 hr late"


def test_due_status_blockers() -> None:
    now = datetime(2026, 10, 14, 9, 0)
    assert hc.due_status(habit(), now) is None  # anytime
    assert hc.due_status(habit(time="08:00", reminder=False), now) is None
    assert hc.due_status(habit(time="08:00", archived=True), now) is None
    assert hc.due_status(habit(time="08:00"), now, log="done") is None
    assert hc.due_status(habit(time="08:00"), now, log=logs(1, {WED: "skipped"})[0]) is None
    assert hc.due_status(habit(time="08:00", days=96), now) is None  # not scheduled Wednesday
    assert hc.due_status(habit(time="08:00"), now, auto_done=True) is None
    assert hc.due_status(habit(time="08:00", created=datetime(2026, 10, 14, 8, 30)), now) is None


def test_due_status_snooze() -> None:
    h = habit(time="08:00")
    now = datetime(2026, 10, 14, 9, 0)
    assert hc.due_status(h, now, None, datetime(2026, 10, 14, 9, 10)) is None
    assert hc.due_status(h, now, None, datetime(2026, 10, 14, 9, 0)) == "1 hr late"
    assert hc.due_status(h, now, None, datetime(2026, 10, 14, 8, 45)) == "1 hr late"


def test_due_status_uses_logical_day() -> None:
    # Tuesday-only habit at 23:30; at 00:30 Wednesday (logical Tuesday with day start 4) it is due
    tue = habit(time="23:30", days=0b10)
    now = datetime(2026, 10, 14, 0, 30)
    assert hc.due_status(tue, now, day_start_hour=4) == "1 hr late"
    assert hc.due_status(tue, now, day_start_hour=0) is None


# --------------------------------------------------------------------------- grouping


@pytest.mark.parametrize(
    ("time", "dsh", "group"),
    [
        (None, 4, "anytime"),
        ("05:00", 4, "morning"),
        ("11:59", 4, "morning"),
        ("12:00", 4, "afternoon"),
        ("16:59", 4, "afternoon"),
        ("17:00", 4, "evening"),
        ("23:30", 4, "evening"),
        ("01:00", 4, "evening"),
        ("01:00", 0, "morning"),
        ("bogus", 4, "anytime"),
    ],
)
def test_group_of(time: str | None, dsh: int, group: str) -> None:
    assert hc.group_of(habit(time=time), day_start_hour=dsh) == group


def test_sort_key_orders_by_group_time_position() -> None:
    items = [
        habit(1, time="18:00"),
        habit(2, time=None, position=5),
        habit(3, time="07:30"),
        habit(4, time="01:00"),
        habit(5, time=None, position=1),
        habit(6, time="07:30", position=-1),
        habit(7, time="12:15"),
    ]
    ordered = [h.id for h in sorted(items, key=lambda h: hc.sort_key(h, day_start_hour=4))]
    assert ordered == [6, 3, 7, 1, 4, 5, 2]


# --------------------------------------------------------------------------- focus habits


def _session(sid: int, minutes: int, outcome: Outcome, project: str | None) -> Session:
    start = datetime(2026, 10, 14, 9, 0)
    return Session(sid, "Write", project, None, start, start + timedelta(minutes=minutes), minutes * 60, minutes * 60, 0, outcome)


def test_focused_minutes_counts_only_counted_outcomes_and_filters_project() -> None:
    sessions = [
        _session(1, 25, Outcome.COMPLETED, "Thesis"),
        _session(2, 20, Outcome.ENDED_EARLY, "thesis "),
        _session(3, 50, Outcome.ABANDONED, "Thesis"),
        _session(4, 10, Outcome.ACTIVE, "Thesis"),
        _session(5, 30, Outcome.COMPLETED, "Other"),
        _session(6, 5, Outcome.COMPLETED, None),
    ]
    assert hc.focused_minutes(sessions) == 80
    assert hc.focused_minutes(sessions, "Thesis") == 45
    assert hc.focused_minutes(sessions, "  ") == 80
    assert hc.focused_minutes([], "x") == 0


def test_auto_satisfied() -> None:
    auto = habit(focus_minutes=60)
    assert hc.auto_satisfied(auto, 60) and not hc.auto_satisfied(auto, 59)
    assert not hc.auto_satisfied(habit(), 500)


def test_today_counts() -> None:
    hs = [habit(1), habit(2), habit(3), habit(4, days=96), habit(5, archived=True)]
    lg = logs(1, {WED: "done"}) + logs(2, {WED: "skipped"}) + logs(4, {WED: "done"}) + logs(3, {days_back(1): "done"})
    assert hc.today_counts(hs, lg, WED) == (1, 1, 3)


@pytest.mark.parametrize(
    ("mask", "label"),
    [(127, "Every day"), (31, "Weekdays"), (96, "Weekends"), (0, "No days"), (0b0010101, "Mon, Wed, Fri"), (0b1000000, "Sun")],
)
def test_days_label(mask: int, label: str) -> None:
    assert hc.days_label(mask) == label


def test_constants() -> None:
    assert hc.LATENESS_CAP_MINUTES == 120
    assert hc.SNOOZE_MINUTES == 15
    assert hc.MAX_SNOOZES_PER_DAY == 3
    assert hc.GROUPS == ("morning", "afternoon", "evening", "anytime")
