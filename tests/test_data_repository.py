from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from madhura.core.clock import FakeClock
from madhura.core.models import (
    Capture,
    CheckIn,
    CheckInResponse,
    Habit,
    HabitLog,
    HabitStatus,
    Outcome,
    PlannedTask,
    Session,
)
from madhura.data.repository import Repository

TODAY = date(2026, 10, 14)
T0 = datetime(2026, 10, 14, 10, 0)


def make_session(repo: Repository, task: str = "Write", minutes: int = 25, start: datetime = T0, **fields) -> int:
    sid = repo.create_session(
        task, fields.pop("project", None), fields.pop("planned_task_id", None), start, minutes * 60
    )
    if fields:
        repo.update_session(sid, **fields)
    return sid


# ================================================================== sessions


def test_create_get_update_session(repo: Repository) -> None:
    sid = repo.create_session("Write spec", "Madhura", 7, T0, 3600)
    s = repo.get_session(sid)
    assert isinstance(s, Session)
    assert (s.task, s.project, s.planned_task_id) == ("Write spec", "Madhura", 7)
    assert s.started_at == T0
    assert s.ended_at is None
    assert (s.planned_seconds, s.focused_seconds, s.paused_seconds) == (3600, 0, 0)
    assert s.outcome is Outcome.ACTIVE
    assert s.note is None

    repo.update_session(
        sid,
        ended_at=T0 + timedelta(minutes=50),
        focused_seconds=2999.6,
        paused_seconds=12.2,
        outcome="ended_early",
        note="good",
        project="  ",
    )
    s = repo.get_session(sid)
    assert s.focused_seconds == 3000
    assert s.paused_seconds == 12
    assert s.outcome is Outcome.ENDED_EARLY
    assert s.ended_at == datetime(2026, 10, 14, 10, 50)
    assert s.note == "good"
    assert s.project is None
    repo.update_session(sid)  # no fields → no-op
    with pytest.raises(ValueError):
        repo.update_session(sid, bogus=1)
    with pytest.raises(ValueError):
        repo.update_session(sid, outcome="exploded")
    assert repo.get_session(99999) is None


def test_delete_session_cascades_checkins_and_nulls_captures(repo: Repository) -> None:
    sid = make_session(repo)
    repo.add_checkin(sid, T0, "focused")
    cap = repo.add_capture("call mom", session_id=sid, at=T0)
    repo.delete_session(sid)
    assert repo.get_session(sid) is None
    assert repo.checkins_for_session(sid) == []
    captures = repo.open_captures()
    assert [c.id for c in captures] == [cap]
    assert captures[0].session_id is None


def test_sessions_between_and_recent(repo: Repository) -> None:
    a = make_session(repo, "A", start=T0, outcome="completed", focused_seconds=1500)
    b = make_session(repo, "B", start=T0 + timedelta(hours=1), outcome="ended_early", focused_seconds=600)
    c = make_session(repo, "C", start=T0 + timedelta(hours=2), outcome="abandoned", focused_seconds=100)
    d = make_session(repo, "D", start=T0 + timedelta(hours=3))  # active
    e = make_session(repo, "E", start=T0 + timedelta(days=1), outcome="completed")
    start, end = T0, T0 + timedelta(days=1)
    assert [s.id for s in repo.sessions_between(start, end)] == [a, b]
    assert [s.id for s in repo.sessions_between(start, end, outcomes=None)] == [a, b, c, d]
    assert [s.id for s in repo.sessions_between(start, end, outcomes=("abandoned",))] == [c]
    assert [s.id for s in repo.sessions_between(start, end, outcomes=(Outcome.COMPLETED,))] == [a]
    assert repo.sessions_between(start, end, outcomes=()) == []
    assert [s.id for s in repo.sessions_between(start, end + timedelta(seconds=1))] == [a, b, e]

    assert [s.id for s in repo.recent_sessions()] == [e, c, b, a]
    assert [s.id for s in repo.recent_sessions(include_abandoned=False)] == [e, b, a]
    assert [s.id for s in repo.recent_sessions(limit=2)] == [e, c]
    assert [s.id for s in repo.recent_sessions(start_dt=T0 + timedelta(minutes=30), end_dt=end)] == [c, b]
    assert [s.id for s in repo.active_sessions()] == [d]


def test_focus_seconds_by_day(repo: Repository) -> None:
    """Per logical day, counted outcomes only, summed in SQL; same days as core.days.logical_date."""
    from madhura.core.days import logical_date

    late = datetime(2026, 10, 14, 2, 30)  # before the 04:00 day start → Tue 13 Oct
    a = make_session(repo, start=late, outcome="completed", focused_seconds=1800)
    make_session(repo, start=datetime(2026, 10, 13, 9, 0), outcome="ended_early", focused_seconds=600)
    make_session(repo, start=datetime(2026, 10, 13, 10, 0), outcome="abandoned", focused_seconds=900)
    make_session(repo, start=datetime(2026, 10, 13, 11, 0))  # active
    b = make_session(repo, start=T0, outcome="completed", focused_seconds=1500)
    make_session(repo, start=datetime(1999, 12, 31, 23, 0), outcome="completed", focused_seconds=60)
    make_session(repo, start=datetime(2026, 10, 15, 4, 0), outcome="completed", focused_seconds=99)  # at end: out
    end = datetime(2026, 10, 15, 4, 0)
    assert logical_date(late, 4) == date(2026, 10, 13)
    assert repo.focus_seconds_by_day(end, 4) == {
        date(1999, 12, 31): 60, date(2026, 10, 13): 2400, date(2026, 10, 14): 1500,
    }
    assert repo.focus_seconds_by_day(end, 0) == {
        date(1999, 12, 31): 60, date(2026, 10, 13): 600, date(2026, 10, 14): 3300,
    }
    assert repo.focus_seconds_by_day(end, 4, {a, b}) == {date(1999, 12, 31): 60, date(2026, 10, 13): 600}
    assert repo.focus_seconds_by_day(datetime(2000, 1, 1), 4, [999]) == {date(1999, 12, 31): 60}


def test_recent_titles_and_projects(repo: Repository, fake_clock: FakeClock) -> None:
    make_session(repo, "Old", start=T0 - timedelta(days=2), project="Alpha")
    make_session(repo, "Newer", start=T0 - timedelta(days=1), project="Beta")
    make_session(repo, "Old", start=T0 - timedelta(hours=1), project="Alpha")
    make_session(repo, "   ", start=T0 - timedelta(hours=1))
    fake_clock.set(T0 + timedelta(hours=1))
    repo.add_task("Planned thing", project="Gamma", plan_date=TODAY)
    repo.add_habit("Read", project="Delta")
    assert repo.recent_task_titles() == ["Planned thing", "Old", "Newer"]
    assert repo.recent_task_titles(limit=1) == ["Planned thing"]
    assert repo.projects()[:3] in (["Delta", "Gamma", "Alpha"], ["Gamma", "Delta", "Alpha"])
    assert set(repo.projects()) == {"Alpha", "Beta", "Gamma", "Delta"}


def test_save_engine_checkpoint_is_transactional(repo: Repository) -> None:
    sid = make_session(repo)
    repo.save_engine_checkpoint({"v": 1, "state": "focus"}, sid, focused_seconds=120.4, paused_seconds=3)
    assert repo.kv_get("engine_snapshot") == {"v": 1, "state": "focus"}
    assert repo.get_session(sid).focused_seconds == 120
    # A failing session update must roll back the snapshot write too.
    with pytest.raises(ValueError):
        repo.save_engine_checkpoint({"v": 1, "state": "paused"}, sid, nope=1)
    assert repo.kv_get("engine_snapshot") == {"v": 1, "state": "focus"}
    repo.save_engine_checkpoint(None, None)
    assert repo.kv_get("engine_snapshot") is None


# ================================================================== check-ins


def test_checkins(repo: Repository) -> None:
    sid = make_session(repo)
    other = make_session(repo, "Other")
    c1 = repo.add_checkin(sid, T0 + timedelta(minutes=7), "focused")
    repo.add_checkin(sid, T0 + timedelta(minutes=14), CheckInResponse.DRIFTED, reason="Phone")
    repo.add_checkin(sid, T0 + timedelta(minutes=21), "drifted", reason="  ")
    repo.add_checkin(other, T0 + timedelta(minutes=30), "missed")
    items = repo.checkins_for_session(sid)
    assert [c.id for c in items][0] == c1
    assert all(isinstance(c, CheckIn) for c in items)
    assert items[0].response is CheckInResponse.FOCUSED
    assert items[0].response == "focused"
    assert items[1].reason == "Phone"
    assert items[2].reason is None
    assert repo.checkin_counts(sid) == {"focused": 1, "drifted": 2, "missed": 0}
    assert repo.checkin_counts(12345) == {"focused": 0, "drifted": 0, "missed": 0}
    window = repo.checkins_between(T0 + timedelta(minutes=10), T0 + timedelta(minutes=30))
    assert [c.response for c in window] == ["drifted", "drifted"]
    with pytest.raises(ValueError):
        repo.add_checkin(sid, T0, "asleep")
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_checkin(99999, T0, "focused")


# ================================================================== tasks


def test_tasks_crud_and_positions(repo: Repository, fake_clock: FakeClock) -> None:
    a = repo.add_task("A", plan_date=TODAY)
    b = repo.add_task("B", minutes=45, project="P", plan_date=TODAY)
    c = repo.add_task("C", plan_date=TODAY, position=0)  # insert at top
    tasks = repo.tasks_for_date(TODAY)
    assert [t.id for t in tasks] == [c, a, b]
    assert [t.position for t in tasks] == [0, 1, 2]
    task_b = repo.get_task(b)
    assert isinstance(task_b, PlannedTask)
    assert (task_b.minutes, task_b.project, task_b.done, task_b.plan_date) == (45, "P", False, TODAY)
    assert task_b.created_at == T0
    with pytest.raises(ValueError):
        repo.add_task("   ", plan_date=TODAY)

    repo.update_task(a, title="A2", minutes=30, project="")
    task_a = repo.get_task(a)
    assert (task_a.title, task_a.minutes, task_a.project) == ("A2", 30, None)
    with pytest.raises(ValueError):
        repo.update_task(a, colour="red")

    fake_clock.advance(60)
    repo.set_task_done(a, True)
    assert repo.get_task(a).done is True
    assert repo.get_task(a).completed_at == T0 + timedelta(minutes=1)
    assert [t.id for t in repo.tasks_for_date(TODAY, include_done=False)] == [c, b]
    repo.set_task_done(a, True, at=datetime(2026, 10, 14, 12, 0))
    assert repo.get_task(a).completed_at == datetime(2026, 10, 14, 12, 0)
    repo.set_task_done(a, False)
    assert repo.get_task(a).completed_at is None

    repo.reorder_tasks([b, a, c])
    assert [t.id for t in repo.tasks_for_date(TODAY)] == [b, a, c]
    repo.delete_task(c)
    assert repo.get_task(c) is None


def test_add_task_default_plan_date_uses_today_fn(repo: Repository) -> None:
    tid = repo.add_task("No date")
    assert repo.get_task(tid).plan_date == TODAY
    repo.today_fn = lambda: date(2030, 1, 1)
    tid2 = repo.add_task("Custom")
    assert repo.get_task(tid2).plan_date == date(2030, 1, 1)


def test_unfinished_before_and_move(repo: Repository) -> None:
    y = TODAY - timedelta(days=1)
    old1 = repo.add_task("old1", plan_date=y - timedelta(days=1))
    old2 = repo.add_task("old2", plan_date=y)
    done_old = repo.add_task("done", plan_date=y)
    repo.set_task_done(done_old, True)
    today_task = repo.add_task("today", plan_date=TODAY)
    assert [t.id for t in repo.unfinished_before(TODAY)] == [old1, old2]
    repo.move_tasks_to_date([old2, old1], TODAY)
    assert [t.id for t in repo.tasks_for_date(TODAY)] == [today_task, old2, old1]
    assert [t.position for t in repo.tasks_for_date(TODAY)] == [0, 1, 2]
    assert repo.unfinished_before(TODAY) == []


# ================================================================== habits


def test_habits_crud(repo: Repository) -> None:
    h1 = repo.add_habit("Meditate", emoji="🧘", cue="After coffee", time="07:30", days=31)
    h2 = repo.add_habit("Deep work", focus_minutes=60, project="Madhura", reminder=False, urgent=True)
    habit = repo.get_habit(h1)
    assert isinstance(habit, Habit)
    assert (habit.name, habit.emoji, habit.cue, habit.time, habit.days) == (
        "Meditate",
        "🧘",
        "After coffee",
        "07:30",
        31,
    )
    assert habit.reminder is True and habit.urgent is False and habit.archived is False
    assert habit.position == 0
    deep = repo.get_habit(h2)
    assert (deep.focus_minutes, deep.project, deep.reminder, deep.urgent, deep.position) == (
        60,
        "Madhura",
        False,
        True,
        1,
    )
    with pytest.raises(ValueError):
        repo.add_habit("  ")

    repo.update_habit(h1, archived=True, cue="", time=None)
    assert [h.id for h in repo.habits()] == [h2]
    assert [h.id for h in repo.habits(include_archived=True)] == [h1, h2]
    assert repo.get_habit(h1).cue is None
    with pytest.raises(ValueError):
        repo.update_habit(h1, colour="x")
    repo.update_habit(h2, position=-1)
    assert [h.id for h in repo.habits(include_archived=True)] == [h2, h1]


def test_habit_logs_upsert_delete_and_cascade(repo: Repository, fake_clock: FakeClock) -> None:
    h = repo.add_habit("Read")
    other = repo.add_habit("Walk")
    repo.set_habit_status(h, TODAY, "done")
    repo.set_habit_status(h, TODAY, HabitStatus.SKIPPED, at=datetime(2026, 10, 14, 11, 0))
    logs = repo.habit_logs_for_date(TODAY)
    assert len(logs) == 1
    assert isinstance(logs[0], HabitLog)
    assert logs[0].status is HabitStatus.SKIPPED and logs[0].status == "skipped"
    assert logs[0].at == datetime(2026, 10, 14, 11, 0)
    for offset in range(1, 4):
        repo.set_habit_status(other, TODAY - timedelta(days=offset), "done")
    between = repo.habit_logs_between(TODAY - timedelta(days=3), TODAY - timedelta(days=1))
    assert len(between) == 3  # inclusive on both ends
    assert [log.date for log in between] == [
        TODAY - timedelta(days=3),
        TODAY - timedelta(days=2),
        TODAY - timedelta(days=1),
    ]
    with pytest.raises(ValueError):
        repo.set_habit_status(h, TODAY, "maybe")
    repo.set_habit_status(h, TODAY, None)
    assert repo.habit_logs_for_date(TODAY) == []
    repo.delete_habit(other)
    assert repo.habit_logs_between(TODAY - timedelta(days=10), TODAY) == []
    assert repo.get_habit(other) is None


# ================================================================== captures & notes


def test_captures(repo: Repository, fake_clock: FakeClock) -> None:
    c1 = repo.add_capture("  idea one ")
    fake_clock.advance(5)
    c2 = repo.add_capture("idea two", at=T0 + timedelta(minutes=1))
    caps = repo.open_captures()
    assert [c.id for c in caps] == [c1, c2]
    assert isinstance(caps[0], Capture)
    assert caps[0].text == "idea one"
    assert caps[0].at == T0
    with pytest.raises(ValueError):
        repo.add_capture("   ")
    repo.set_capture_done(c1, True)
    assert [c.id for c in repo.open_captures()] == [c2]
    repo.set_capture_done(c1, False)
    assert [c.id for c in repo.open_captures()] == [c1, c2]
    repo.delete_capture(c2)
    assert [c.id for c in repo.open_captures()] == [c1]


def test_day_notes(repo: Repository) -> None:
    assert repo.get_day_note(TODAY) is None
    repo.set_day_note(TODAY, "Shipped it")
    assert repo.get_day_note(TODAY) == "Shipped it"
    repo.set_day_note(TODAY, "Shipped it twice")
    assert repo.get_day_note(TODAY) == "Shipped it twice"
    repo.set_day_note(TODAY, "  ")
    assert repo.get_day_note(TODAY) is None
    repo.set_day_note(TODAY, "x")
    repo.set_day_note(TODAY, None)
    assert repo.get_day_note(TODAY) is None


# ================================================================== kv


def test_kv(repo: Repository) -> None:
    assert repo.kv_get("missing") is None
    assert repo.kv_get("missing", 5) == 5
    repo.kv_set("dict", {"a": [1, 2], "b": None})
    assert repo.kv_get("dict") == {"a": [1, 2], "b": None}
    repo.kv_set("str", "2026-10-14T10:00:00")
    assert repo.kv_get("str") == "2026-10-14T10:00:00"
    repo.kv_set("date", TODAY)
    assert repo.kv_get("date") == "2026-10-14"
    repo.kv_set("false", False)
    assert repo.kv_get("false") is False
    repo.kv_set("dict", None)
    assert repo.kv_get("dict", "gone") == "gone"
    repo.conn.execute("INSERT INTO kv(key, value) VALUES ('bad', '{not json')")
    assert repo.kv_get("bad", "default") == "default"
    with pytest.raises(TypeError):
        repo.kv_set("obj", object())


# ================================================================== transactions & maintenance


def test_transaction_commit_and_rollback(repo: Repository) -> None:
    with repo.transaction():
        repo.kv_set("a", 1)
        assert repo.in_transaction
    assert not repo.in_transaction
    assert repo.kv_get("a") == 1
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.kv_set("b", 2)
            raise RuntimeError("boom")
    assert repo.kv_get("b") is None
    assert not repo.conn.in_transaction


def test_nested_transaction_uses_savepoints(repo: Repository) -> None:
    with repo.transaction():
        repo.kv_set("outer", 1)
        with pytest.raises(KeyError):
            with repo.transaction():
                repo.kv_set("inner", 2)
                raise KeyError("x")
        assert repo.kv_get("inner") is None
        with repo.transaction():
            repo.kv_set("inner2", 3)
    assert repo.kv_get("outer") == 1
    assert repo.kv_get("inner2") == 3
    with pytest.raises(ValueError):
        with repo.transaction():
            repo.kv_set("outer2", 1)
            with repo.transaction():
                repo.kv_set("inner3", 1)
            raise ValueError
    assert repo.kv_get("outer2") is None and repo.kv_get("inner3") is None


def test_export_dict(repo: Repository) -> None:
    sid = make_session(repo, outcome="completed")
    repo.add_checkin(sid, T0, "focused")
    repo.add_task("t", plan_date=TODAY)
    h = repo.add_habit("h")
    repo.set_habit_status(h, TODAY, "done")
    repo.add_capture("c")
    repo.set_day_note(TODAY, "note")
    repo.kv_set("k", {"v": 1})
    data = repo.export_dict()
    json.dumps(data)  # JSON-ready
    assert data["version"] == "2.0.0"
    assert data["schema_version"] == 1
    for table in ("sessions", "checkins", "tasks", "habits", "habit_logs", "captures", "day_notes"):
        assert len(data[table]) == 1, table
    assert data["sessions"][0]["task"] == "Write"
    assert data["kv"]["k"] == {"v": 1}


def test_reset_all_keeps_recovered_from(repo: Repository) -> None:
    sid = make_session(repo)
    repo.add_checkin(sid, T0, "focused")
    repo.add_task("t", plan_date=TODAY)
    h = repo.add_habit("h")
    repo.set_habit_status(h, TODAY, "done")
    repo.add_capture("c")
    repo.set_day_note(TODAY, "n")
    repo.kv_set("recovered_from", "madhura.corrupt-x.db")
    repo.kv_set("other", 1)
    repo.reset_all()
    for table in ("sessions", "checkins", "tasks", "habits", "habit_logs", "captures", "day_notes"):
        assert repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert repo.kv_get("other") is None
    assert repo.kv_get("recovered_from") == "madhura.corrupt-x.db"


def test_counted_seconds_between(repo: Repository) -> None:
    make_session(repo, start=T0, outcome="completed", focused_seconds=1500, project="A")
    make_session(repo, start=T0, outcome="ended_early", focused_seconds=600, project="B")
    make_session(repo, start=T0, outcome="abandoned", focused_seconds=900)
    make_session(repo, start=T0, focused_seconds=900)  # active
    end = T0 + timedelta(days=1)
    assert repo.counted_seconds_between(T0, end) == 2100
    assert repo.counted_seconds_between(T0, end, project="A") == 1500


def test_repository_variants(tmp_path) -> None:
    mem = Repository(":memory:")
    assert mem.path is None
    mem.kv_set("x", 1)
    assert mem.kv_get("x") == 1
    mem.close()
    conn = sqlite3.connect(tmp_path / "raw.db", isolation_level=None)
    wrapped = Repository(conn)
    wrapped.add_capture("hi")
    assert len(wrapped.open_captures()) == 1
    wrapped.close()
    opened = Repository.open(tmp_path / "o.db")
    assert opened.path == tmp_path / "o.db"
    opened.close()
