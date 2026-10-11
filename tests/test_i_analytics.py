"""core.analytics — pure aggregations for Insights, the weekly recap and the wrap-up [I]."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from madhura.core import analytics as an
from madhura.core.models import CheckIn, CheckInResponse, Habit, HabitLog, HabitStatus, Outcome, Session

WED = date(2026, 10, 14)  # a Wednesday


def S(  # noqa: N802 - tiny factory
    start: datetime,
    focused_min: float,
    *,
    outcome: str = "completed",
    task: str = "Write",
    project: str | None = None,
    sid: int = 0,
    paused_min: float = 0,
    ended: datetime | None = None,
) -> Session:
    focused = int(round(focused_min * 60))
    end = ended if ended is not None else start + timedelta(seconds=focused + paused_min * 60)
    return Session(
        id=sid,
        task=task,
        project=project,
        planned_task_id=None,
        started_at=start,
        ended_at=end,
        planned_seconds=focused,
        focused_seconds=focused,
        paused_seconds=int(paused_min * 60),
        outcome=Outcome(outcome),
    )


def C(response: str, reason: str | None = None, sid: int = 1) -> CheckIn:  # noqa: N802
    return CheckIn(0, sid, datetime(2026, 10, 14, 10, 0), CheckInResponse(response), reason)


def H(hid: int, days: int = 127, created: datetime = datetime(2026, 1, 1, 9), archived: bool = False) -> Habit:  # noqa: N802
    return Habit(hid, f"h{hid}", None, None, None, days, True, False, None, None, hid, archived, created)


def L(hid: int, d: date, status: str = "done") -> HabitLog:  # noqa: N802
    return HabitLog(0, hid, d, HabitStatus(status), datetime.combine(d, datetime.min.time()))


# --------------------------------------------------------------------------- counting rule


def test_counted_filters_outcomes_and_accepts_strings() -> None:
    rows = [S(datetime(2026, 10, 14, 9), 30, outcome=o) for o in ("completed", "ended_early", "abandoned", "active")]
    assert [s.outcome for s in an.counted(rows)] == [Outcome.COMPLETED, Outcome.ENDED_EARLY]
    assert an.total_seconds(rows) == 3600


# --------------------------------------------------------------------------- periods


@pytest.mark.parametrize(
    ("key", "start", "days"),
    [("7d", date(2026, 10, 8), 7), ("30d", date(2026, 9, 15), 30), ("12w", date(2026, 7, 27), 80)],
)
def test_range_period(key: str, start: date, days: int) -> None:
    p = an.range_period(key, WED)
    assert p.start == start and p.end == WED and p.days == days
    assert p.contains(WED) and not p.contains(WED + timedelta(days=1))


def test_range_period_unknown_key_is_7_days() -> None:
    assert an.range_period("nope", WED) == an.range_period("7d", WED)


def test_12w_range_starts_on_a_monday_and_covers_12_weeks() -> None:
    p = an.range_period("12w", WED)
    assert p.start.weekday() == 0
    assert (WED - p.start).days // 7 == 11


def test_previous_periods_are_adjacent_or_week_aligned() -> None:
    p7 = an.previous_range_period("7d", WED)
    assert p7 == an.Period(date(2026, 10, 1), date(2026, 10, 7))
    p30 = an.previous_range_period("30d", WED)
    assert p30.end == date(2026, 9, 14) and p30.days == 30
    p12 = an.previous_range_period("12w", WED)
    cur = an.range_period("12w", WED)
    assert p12.days == cur.days
    assert p12.start == cur.start - timedelta(weeks=12)
    assert p12.start.weekday() == 0 and p12.end.weekday() == WED.weekday()


def test_period_bounds_follow_day_start_hour() -> None:
    p = an.Period(date(2026, 10, 13), date(2026, 10, 14))
    assert an.period_bounds(p, 4) == (datetime(2026, 10, 13, 4), datetime(2026, 10, 15, 4))
    assert an.period_bounds(p, 0) == (datetime(2026, 10, 13, 0), datetime(2026, 10, 15, 0))


# --------------------------------------------------------------------------- daily / weekly buckets


def test_daily_totals_use_logical_date_of_start() -> None:
    sessions = [
        S(datetime(2026, 10, 14, 1, 30), 60),  # before 04:00 → belongs to the 13th
        S(datetime(2026, 10, 14, 4, 0), 30),  # exactly day start → 14th
        S(datetime(2026, 10, 14, 23, 30), 90),  # runs past midnight, still the 14th
        S(datetime(2026, 10, 14, 10, 0), 45, outcome="abandoned"),
    ]
    totals = dict(an.daily_totals(sessions, date(2026, 10, 12), date(2026, 10, 15), 4))
    assert totals == {
        date(2026, 10, 12): 0,
        date(2026, 10, 13): 3600,
        date(2026, 10, 14): 30 * 60 + 90 * 60,
        date(2026, 10, 15): 0,
    }
    # With day_start_hour 0 the 01:30 session is the 14th.
    totals0 = dict(an.daily_totals(sessions, date(2026, 10, 13), date(2026, 10, 14), 0))
    assert totals0[date(2026, 10, 13)] == 0
    assert totals0[date(2026, 10, 14)] == (60 + 30 + 90) * 60


def test_daily_stats_count_sessions_and_zero_fill() -> None:
    sessions = [S(datetime(2026, 10, 14, 9), 25), S(datetime(2026, 10, 14, 11), 50), S(datetime(2026, 10, 10, 9), 10)]
    stats = an.daily_stats(sessions, date(2026, 10, 8), WED, 4)
    assert len(stats) == 7 and stats[0].day == date(2026, 10, 8) and stats[-1].day == WED
    assert stats[-1].seconds == 75 * 60 and stats[-1].sessions == 2
    assert stats[2].seconds == 600 and stats[2].sessions == 1
    assert sum(st.sessions for st in stats) == 3


def test_daily_totals_ignore_out_of_range() -> None:
    sessions = [S(datetime(2026, 9, 1, 9), 25), S(datetime(2026, 11, 1, 9), 25)]
    assert all(v == 0 for _d, v in an.daily_totals(sessions, date(2026, 10, 8), WED, 4))


def test_weekly_totals_bucket_by_iso_week_of_logical_date() -> None:
    sessions = [
        S(datetime(2026, 10, 12, 9), 60),  # Monday 12 Oct
        S(datetime(2026, 10, 18, 22), 30),  # Sunday same week
        S(datetime(2026, 10, 19, 2), 20),  # Mon 02:00 → logical Sunday 18th → same week
        S(datetime(2026, 10, 19, 9), 40),  # next week
        S(datetime(2026, 10, 5, 9), 15),
    ]
    weeks = an.weekly_totals(sessions, date(2026, 10, 7), 3, 4)  # start week normalised to Monday 5 Oct
    assert [w for w, _ in weeks] == [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)]
    assert [v for _w, v in weeks] == [15 * 60, (60 + 30 + 20) * 60, 40 * 60]
    stats = an.weekly_stats(sessions, date(2026, 10, 5), 3, 4)
    assert [st.sessions for st in stats] == [1, 3, 1]
    assert an.weekly_totals(sessions, date(2026, 10, 5), 0, 4) == []


def test_best_day() -> None:
    totals = [(date(2026, 10, 1), 0), (date(2026, 10, 2), 300), (date(2026, 10, 3), 300), (date(2026, 10, 4), 100)]
    assert an.best_day(totals) == date(2026, 10, 2)
    assert an.best_day([(date(2026, 10, 1), 0)]) is None
    assert an.best_day([]) is None


# --------------------------------------------------------------------------- heatmap


def test_hour_matrix_splits_across_hour_boundaries() -> None:
    m = an.hour_weekday_matrix([S(datetime(2026, 10, 14, 9, 40), 45)])  # Wed 09:40–10:25
    assert m[2][9] == pytest.approx(20 * 60)
    assert m[2][10] == pytest.approx(25 * 60)
    assert sum(sum(r) for r in m) == pytest.approx(45 * 60)


def test_hour_matrix_crosses_midnight_into_next_weekday() -> None:
    m = an.hour_weekday_matrix([S(datetime(2026, 10, 18, 23, 30), 60)])  # Sunday 23:30 → Monday 00:30
    assert m[6][23] == pytest.approx(30 * 60)
    assert m[0][0] == pytest.approx(30 * 60)
    assert an.matrix_has_early_hours(m)
    tiny = an.hour_weekday_matrix([S(datetime(2026, 10, 18, 23, 59, 30), 1)])  # 30 s after midnight
    assert not an.matrix_has_early_hours(tiny)


def test_hour_matrix_never_credits_pauses() -> None:
    # 30 min of focus, then the laptop slept for hours: only 09:00–09:30 is credited.
    s = S(datetime(2026, 10, 14, 9, 0), 30, paused_min=8 * 60)
    m = an.hour_weekday_matrix([s])
    assert m[2][9] == pytest.approx(30 * 60)
    assert sum(sum(r) for r in m) == pytest.approx(30 * 60)
    assert not an.matrix_has_early_hours(m)


def test_hour_matrix_counting_rule_and_shape() -> None:
    m = an.hour_weekday_matrix([S(datetime(2026, 10, 14, 9), 30, outcome="abandoned"), S(datetime(2026, 10, 14, 9), 0)])
    assert len(m) == 7 and all(len(r) == 24 for r in m)
    assert sum(sum(r) for r in m) == 0


def test_heatmap_levels_quartiles_of_peak() -> None:
    m = [[0.0] * 24 for _ in range(7)]
    m[0][9] = 100
    m[0][10] = 76
    m[0][11] = 75
    m[0][12] = 50
    m[0][13] = 25
    m[0][14] = 1
    lv = an.heatmap_levels(m)
    assert lv[0][9:15] == [4, 4, 3, 2, 1, 1]
    assert lv[1][0] == 0
    assert an.heatmap_levels([[0.0] * 24] * 7)[3][3] == 0


# --------------------------------------------------------------------------- check-ins


def test_focus_score_and_came_back() -> None:
    cs = [C("focused")] * 6 + [C("drifted", "Phone"), C("missed"), C("missed")]
    assert an.focus_score(cs) == pytest.approx(6 / 7)
    assert an.came_back(cs) == 1
    assert an.checkin_counts(cs) == {"focused": 6, "drifted": 1, "missed": 2}
    assert an.focus_score([C("missed")]) is None
    assert an.focus_score([]) is None
    assert an.came_back([]) == 0


def test_drift_reasons_sorted_with_chip_order_ties() -> None:
    cs = [
        C("drifted", "Email"),
        C("drifted", "Phone"),
        C("drifted", "Email"),
        C("drifted", "Messages"),
        C("drifted", None),
        C("drifted", "  "),
        C("focused", "Phone"),
        C("drifted", "Zebra"),
    ]
    assert an.drift_reasons(cs) == [("Email", 2), ("Phone", 1), ("Messages", 1), ("Zebra", 1)]


# --------------------------------------------------------------------------- rankings


def test_top_projects_merge_case_and_group_no_project() -> None:
    t = datetime(2026, 10, 14, 9)
    sessions = [
        S(t, 60, project="Madhura"),
        S(t, 30, project="madhura "),
        S(t, 50, project=None),
        S(t, 20, project="Thesis"),
        S(t, 99, project="Thesis", outcome="abandoned"),
    ]
    assert an.top_projects(sessions) == [("Madhura", 90 * 60), (None, 50 * 60), ("Thesis", 20 * 60)]
    assert an.top_projects(sessions, 1) == [("Madhura", 90 * 60)]


def test_top_tasks_and_dominant_project() -> None:
    t = datetime(2026, 10, 14, 9)
    sessions = [
        S(t, 30, task="Write report", project="Work"),
        S(t, 10, task="write report", project="Side"),
        S(t, 25, task="Read"),
    ]
    ranked = an.ranked(sessions, "task")
    assert ranked[0].label == "Write report" and ranked[0].seconds == 40 * 60 and ranked[0].sessions == 2
    assert ranked[0].project == "Work"
    assert an.top_tasks(sessions) == [("Write report", 2400), ("Read", 1500)]


def test_ranking_ties_prefer_more_sessions_then_name() -> None:
    t = datetime(2026, 10, 14, 9)
    sessions = [S(t, 30, project="B"), S(t, 15, project="A"), S(t, 15, project="A"), S(t, 30, project="C")]
    assert [p for p, _ in an.top_projects(sessions)] == ["A", "B", "C"]


# --------------------------------------------------------------------------- deltas & goals


@pytest.mark.parametrize(
    ("curr", "prev", "expected"),
    [(12, 10, 0.2), (8, 10, -0.2), (10, 10, 0.0), (5, 0, None), (0, 0, None), (None, 5, None), (5, None, None), (0, 4, -1.0)],
)
def test_period_delta(curr, prev, expected) -> None:  # noqa: ANN001
    result = an.period_delta(curr, prev)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_goal_dates_sum_per_logical_day() -> None:
    sessions = [
        S(datetime(2026, 10, 14, 9), 60),
        S(datetime(2026, 10, 15, 2), 60),  # logical 14th (day start 4)
        S(datetime(2026, 10, 13, 9), 119),
        S(datetime(2026, 10, 12, 9), 200, outcome="abandoned"),
    ]
    assert an.goal_dates(sessions, 120, 4) == {date(2026, 10, 14)}
    assert an.goal_dates(sessions, 120, 0) == set()
    assert an.goal_dates(sessions, 0, 4) == set()
    assert an.goal_dates(sessions, 60, 0) == {date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 13)}


# --------------------------------------------------------------------------- habits


def test_habit_completion_counts_rules() -> None:
    weekdays = H(1, days=0b0011111)  # Mon–Fri
    daily = H(2)
    new = H(3, created=datetime(2026, 10, 13, 12))  # created Tuesday
    archived = H(4, archived=True)
    start, end = date(2026, 10, 12), WED  # Mon..Wed
    logs = [
        L(1, date(2026, 10, 12)),
        L(1, date(2026, 10, 13), "skipped"),  # neutral
        L(2, date(2026, 10, 12)),
        L(2, date(2026, 10, 13)),
        L(3, date(2026, 10, 14)),  # done today → counts
        L(4, date(2026, 10, 12)),
        L(1, date(2026, 10, 11)),  # Sunday: unscheduled & out of range
    ]
    done, scheduled = an.habit_completion_counts([weekdays, daily, new, archived], logs, start, end, today=WED)
    # weekdays: Mon done, Tue skipped (left out), Wed pending today (left out) → 1/1
    # daily: Mon done, Tue done, Wed pending (left out) → 2/2
    # new: created Tue: Tue missed, Wed done → 1/2
    assert (done, scheduled) == (4, 5)
    assert an.habit_completion([weekdays, daily, new], logs, start, end, today=WED) == pytest.approx(4 / 5)


def test_habit_completion_uses_the_logical_creation_day() -> None:
    # created Thu 15 Oct 01:30 with a 04:00 day start = logical Wednesday, done that day
    h = H(1, created=datetime(2026, 10, 15, 1, 30))
    logs = [L(1, WED)]
    assert an.habit_completion_counts([h], logs, WED, WED, today=WED, day_start_hour=4) == (1, 1)
    assert an.habit_completion_counts([h], logs, date(2026, 10, 12), WED, today=WED, day_start_hour=4) == (1, 1)
    assert an.habit_completion([h], logs, date(2026, 10, 12), WED, today=WED, day_start_hour=4) == pytest.approx(1.0)
    # the next day, a missed Wednesday is a scheduled day too
    thu = WED + timedelta(days=1)
    assert an.habit_completion_counts([h], [], WED, thu, today=thu, day_start_hour=4) == (0, 1)
    # with a midnight day start the habit only exists from Thursday
    assert an.habit_completion_counts([h], logs, WED, WED, today=WED) == (0, 0)


def test_habit_completion_without_today_counts_everything() -> None:
    logs = [L(1, date(2026, 10, 12))]
    assert an.habit_completion_counts([H(1)], logs, date(2026, 10, 12), date(2026, 10, 13)) == (1, 2)
    assert an.habit_completion([], [], date(2026, 10, 12), WED) is None
    assert an.habit_completion([H(1, days=0)], [], date(2026, 10, 12), WED) is None


def test_habit_day_counts_like_the_habits_page() -> None:
    logs = [L(1, WED), L(2, WED, "skipped")]
    habits = [H(1), H(2), H(3, days=0b0000001), H(4, archived=True)]  # 3 = Mondays only
    assert an.habit_day_counts(habits, logs, WED) == (1, 2)


# --------------------------------------------------------------------------- ticks


@pytest.mark.parametrize(
    ("top", "expected"),
    [
        (0, [0, 900, 1800, 2700]),
        (1800, [0, 900, 1800, 2700]),
        (2 * 3600, [0, 3600, 7200, 10800]),
        (3.5 * 3600, [0, 5400, 10800, 16200]),
        (7200 + 1, [0, 3600, 7200, 10800]),
        (10 * 3600, [0, 4 * 3600, 8 * 3600, 12 * 3600]),
    ],
)
def test_nice_ticks(top: float, expected: list[int]) -> None:
    ticks = an.nice_ticks(top)
    assert ticks == expected
    assert ticks[-1] >= top


def test_nice_ticks_huge_values_double() -> None:
    ticks = an.nice_ticks(500 * 3600)
    assert len(ticks) == 4 and ticks[0] == 0 and ticks[-1] >= 500 * 3600
    assert ticks[1] * 3 == ticks[-1]
