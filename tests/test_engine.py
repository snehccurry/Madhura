from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

import pytest

from madhura.core.clock import FakeClock
from madhura.core.models import Outcome
from madhura.services.engine import EngineState, FocusEngine

try:
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    NY = None


class Recorder:
    """Records engine signals in emission order."""

    def __init__(self, engine: FocusEngine) -> None:
        self.events: list[tuple[Any, ...]] = []
        engine.state_changed.connect(lambda s: self.events.append(("state", s)))
        engine.tick.connect(lambda: self.events.append(("tick",)))
        engine.session_started.connect(lambda i: self.events.append(("started", i)))
        engine.session_finished.connect(lambda i, o, r: self.events.append(("finished", i, o, r)))
        engine.session_extended.connect(lambda s: self.events.append(("extended", s)))
        engine.break_started.connect(lambda s: self.events.append(("break_started", s)))
        engine.break_finished.connect(lambda n: self.events.append(("break_finished", n)))
        engine.away_detected.connect(lambda s: self.events.append(("away", s)))

    def without_ticks(self) -> list[tuple[Any, ...]]:
        return [e for e in self.events if e[0] != "tick"]

    def clear(self) -> None:
        self.events.clear()


def run(engine: FocusEngine, clock: FakeClock, seconds: float, step: float = 1.0) -> None:
    """Advance the fake clock in live-sized steps, polling like the timer would."""
    remaining = seconds
    while remaining > 1e-9:
        delta = min(step, remaining)
        clock.advance(delta)
        engine.poll()
        remaining -= delta


# ================================================================== start


def test_start_basics(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    sid = engine.start("  Write the spec  ", 25, project=" Madhura ", planned_task_id=3)
    assert sid is not None
    assert rec.events == [("started", sid), ("state", "focus"), ("tick",)]
    assert engine.state is EngineState.FOCUS
    assert engine.state == "focus"
    assert engine.is_active
    assert (engine.session_id, engine.task, engine.project, engine.planned_task_id) == (
        sid,
        "Write the spec",
        "Madhura",
        3,
    )
    assert engine.planned_seconds == 1500
    assert engine.remaining_whole_seconds == 1500
    assert engine.focused_seconds == 0
    assert engine.progress == 0
    assert engine.started_at == datetime(2026, 10, 14, 10, 0)
    assert engine.ends_at == datetime(2026, 10, 14, 10, 25)
    row = repo.get_session(sid)
    assert row.outcome is Outcome.ACTIVE and row.task == "Write the spec" and row.planned_seconds == 1500
    snap = repo.kv_get("engine_snapshot")
    assert snap["v"] == 1 and snap["state"] == "focus" and snap["session_id"] == sid
    assert set(snap) == {
        "v",
        "state",
        "session_id",
        "task",
        "project",
        "planned_task_id",
        "planned_seconds",
        "focused_before",
        "segment_start",
        "paused_before",
        "pause_start",
        "break_total",
        "break_start",
        "break_label",
        "last_alive",
    }
    run(engine, fake_clock, 90.5)
    assert engine.focused_seconds == pytest.approx(90.5)
    assert engine.remaining_whole_seconds == 1410
    assert engine.progress == pytest.approx(90.5 / 1500)


def test_start_rejections_and_clamps(engine: FocusEngine) -> None:
    assert engine.start("   ", 25) is None
    assert engine.start("", 25) is None
    assert engine.state is EngineState.IDLE
    sid = engine.start("Task", 0)
    assert engine.planned_seconds == 60
    assert engine.start("Another", 25) is None  # already focusing
    engine.pause()
    assert engine.start("Another", 25) is None  # paused counts as active
    engine.abandon()
    engine.start("Long", 10_000)
    assert engine.planned_seconds == 480 * 60
    engine.abandon()
    engine.start("Default", None)
    assert engine.planned_seconds == 60 * 60
    assert sid is not None


# ================================================================== pause / resume


def test_pause_resume(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    engine.start("Task", 25)
    run(engine, fake_clock, 120)
    rec.clear()
    engine.pause()
    assert engine.state is EngineState.PAUSED
    assert rec.without_ticks() == [("state", "paused")]
    assert engine.paused_since == datetime(2026, 10, 14, 10, 2)
    assert repo.kv_get("engine_snapshot")["state"] == "paused"
    run(engine, fake_clock, 300, step=30)
    assert engine.focused_seconds == pytest.approx(120)
    assert engine.paused_seconds == pytest.approx(300)
    assert engine.ends_at == datetime(2026, 10, 14, 10, 7) + timedelta(minutes=23)
    engine.pause()  # no-op
    rec.clear()
    engine.resume()
    assert rec.without_ticks() == [("state", "focus")]
    assert engine.paused_since is None
    run(engine, fake_clock, 60)
    assert engine.focused_seconds == pytest.approx(180)
    engine.resume()  # no-op while focusing
    engine.toggle_pause()
    assert engine.state is EngineState.PAUSED
    engine.toggle_pause()
    assert engine.state is EngineState.FOCUS
    engine.checkpoint()
    row = repo.get_session(engine.session_id)
    assert row.focused_seconds == 180 and row.paused_seconds == 300


def test_pause_at_is_retroactive_and_clamped(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start("Task", 25)
    seg_start = fake_clock.time()
    run(engine, fake_clock, 600, step=30)
    engine.pause(at=fake_clock.time() - 120)
    assert engine.focused_seconds == pytest.approx(480)
    assert engine.paused_seconds == pytest.approx(120)
    engine.resume()
    run(engine, fake_clock, 60)
    engine.pause(at=seg_start)  # before the current segment → clamped to its start
    assert engine.focused_seconds == pytest.approx(480)
    engine.resume()
    run(engine, fake_clock, 10)
    engine.pause(at=fake_clock.time() + 1000)  # future → now
    assert engine.focused_seconds == pytest.approx(490)


# ================================================================== extend / discard


def test_extend(engine: FocusEngine, repo) -> None:
    rec = Recorder(engine)
    assert engine.extend(5) == 0  # idle
    engine.start("Task", 25)
    rec.clear()
    assert engine.extend(5) == 300
    assert rec.events[0] == ("extended", 300)
    assert engine.planned_seconds == 1800
    assert repo.get_session(engine.session_id).planned_seconds == 1800
    assert repo.kv_get("engine_snapshot")["planned_seconds"] == 1800
    engine.pause()
    assert engine.extend(1000) == 480 * 60 - 1800  # capped
    assert engine.planned_seconds == 480 * 60
    assert engine.extend(5) == 0
    assert engine.extend(-5) == 0


def test_discard_focused(engine: FocusEngine, fake_clock: FakeClock) -> None:
    assert engine.discard_focused(10) == 0
    engine.start("Task", 25)
    run(engine, fake_clock, 600, step=30)
    assert engine.discard_focused(240) == pytest.approx(240)
    assert engine.focused_seconds == pytest.approx(360)
    assert engine.paused_seconds == pytest.approx(240)
    run(engine, fake_clock, 30)
    assert engine.focused_seconds == pytest.approx(390)
    engine.pause()
    assert engine.discard_focused(10_000) == pytest.approx(390)
    assert engine.focused_seconds == 0
    assert engine.discard_focused(5) == 0


# ================================================================== finish / abandon


def test_finish_under_60s_deletes_row(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    sid = engine.start("Oops", 25)
    run(engine, fake_clock, 59)
    rec.clear()
    assert engine.finish() == "abandoned"
    assert rec.without_ticks() == [("finished", sid, "abandoned", False), ("state", "idle")]
    assert repo.get_session(sid) is None
    assert engine.state is EngineState.IDLE
    assert repo.kv_get("engine_snapshot") is None
    assert engine.finish() is None


def test_finish_ended_early(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    sid = engine.start("Task", 25)
    run(engine, fake_clock, 600, step=30)
    engine.pause()
    run(engine, fake_clock, 60, step=30)
    assert engine.finish() == "ended_early"
    row = repo.get_session(sid)
    assert row.outcome is Outcome.ENDED_EARLY
    assert row.focused_seconds == 600
    assert row.paused_seconds == 60
    assert row.ended_at == datetime(2026, 10, 14, 10, 11)
    assert engine.task == "" and engine.session_id is None


def test_finish_with_one_second_left_completes(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    sid = engine.start("Task", 25)
    run(engine, fake_clock, 1499, step=30)
    assert engine.finish() == "completed"
    row = repo.get_session(sid)
    assert row.outcome is Outcome.COMPLETED and row.focused_seconds == 1500


def test_abandon(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    assert engine.abandon() is None
    sid = engine.start("Short", 25)
    run(engine, fake_clock, 30)
    assert engine.abandon() == "abandoned"
    assert repo.get_session(sid) is None
    sid2 = engine.start("Longer", 25)
    run(engine, fake_clock, 300, step=30)
    rec = Recorder(engine)
    engine.abandon()
    assert rec.without_ticks() == [("finished", sid2, "abandoned", False), ("state", "idle")]
    row = repo.get_session(sid2)
    assert row.outcome is Outcome.ABANDONED and row.focused_seconds == 300


# ================================================================== natural completion


def test_natural_completion_without_break(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    sid = engine.start("Task", 25)
    run(engine, fake_clock, 1500.4, step=0.7)
    assert engine.state is EngineState.IDLE
    events = rec.without_ticks()
    assert events == [("started", sid), ("state", "focus"), ("finished", sid, "completed", False), ("state", "idle")]
    row = repo.get_session(sid)
    assert row.outcome is Outcome.COMPLETED
    assert row.focused_seconds == 1500
    assert row.ended_at == datetime(2026, 10, 14, 10, 25)
    assert repo.kv_get("engine_snapshot") is None
    assert engine.completed_sessions_today == 1


def test_natural_completion_with_auto_break_order(engine: FocusEngine, settings_store, repo, fake_clock) -> None:
    settings_store.update(auto_start_break=True, break_minutes=7)
    rec = Recorder(engine)
    sid = engine.start("Task", 25)
    state_inside: list[EngineState] = []
    engine.session_finished.connect(lambda *a: state_inside.append(engine.state))
    run(engine, fake_clock, 1500, step=30)
    events = rec.without_ticks()
    assert events[2:] == [("finished", sid, "completed", False), ("break_started", 420), ("state", "break")]
    assert ("state", "idle") not in events
    assert state_inside == [EngineState.BREAK]
    assert engine.state is EngineState.BREAK
    assert engine.break_label == "Break"
    assert engine.break_total_seconds == 420
    snap = repo.kv_get("engine_snapshot")
    assert snap["state"] == "break" and snap["break_total"] == 420


def test_auto_break_respects_breaks_enabled(engine: FocusEngine, settings_store, fake_clock) -> None:
    settings_store.update(auto_start_break=True, breaks_enabled=False)
    engine.start("Task", 1)
    run(engine, fake_clock, 61)
    assert engine.state is EngineState.IDLE


def test_reentrant_start_break_in_session_finished(engine: FocusEngine, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    engine.session_finished.connect(lambda *a: engine.start_break(10, "Lunch"))
    sid = engine.start("Task", 1)
    run(engine, fake_clock, 60)
    events = rec.without_ticks()
    assert events[2:] == [("finished", sid, "completed", False), ("break_started", 600), ("state", "break")]
    assert engine.state is EngineState.BREAK
    assert engine.break_label == "Lunch"


def test_reentrant_start_break_with_auto_break(engine: FocusEngine, settings_store, fake_clock) -> None:
    settings_store.update(auto_start_break=True)
    rec = Recorder(engine)
    engine.session_finished.connect(lambda *a: engine.start_break(15, "Walk"))
    sid = engine.start("Task", 1)
    run(engine, fake_clock, 60)
    events = rec.without_ticks()
    assert events[2:] == [("finished", sid, "completed", False), ("break_started", 900), ("state", "break")]
    assert engine.break_total_seconds == 900 and engine.break_label == "Walk"


def test_reentrant_start_in_session_finished(engine: FocusEngine, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    new_ids: list[int | None] = []
    engine.session_finished.connect(lambda *a: new_ids.append(engine.start("Keep going", 15)))
    sid = engine.start("Task", 1)
    run(engine, fake_clock, 60)
    events = rec.without_ticks()
    new_id = new_ids[0]
    assert new_id is not None and new_id != sid
    assert events[2:] == [("finished", sid, "completed", False), ("started", new_id), ("state", "focus")]
    assert engine.state is EngineState.FOCUS and engine.session_id == new_id


# ================================================================== breaks


def test_break_runs_out_naturally(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    assert engine.start_break() is True
    assert rec.without_ticks() == [("break_started", 300), ("state", "break")]
    assert engine.break_label == "Break"
    assert engine.break_remaining_whole_seconds == 300
    assert engine.ends_at is None
    assert engine.break_ends_at == datetime(2026, 10, 14, 10, 5)
    run(engine, fake_clock, 150, step=30)
    assert engine.break_progress == pytest.approx(0.5)
    rec.clear()
    run(engine, fake_clock, 150, step=30)
    assert rec.without_ticks() == [("break_finished", True), ("state", "idle")]
    assert engine.state is EngineState.IDLE
    assert repo.kv_get("engine_snapshot") is None


def test_break_labels_and_clamps(engine: FocusEngine) -> None:
    engine.start_break(0, "  ")
    assert engine.break_total_seconds == 60 and engine.break_label == "Break"
    engine.start_break(1000, "Meeting")
    assert engine.break_total_seconds == 240 * 60 and engine.break_label == "Meeting"
    engine.skip_break()
    engine.start("Task", 25)
    assert engine.start_break(5) is False


def test_skip_break(engine: FocusEngine) -> None:
    engine.start_break(5, "Lunch")
    rec = Recorder(engine)
    engine.skip_break()
    assert rec.without_ticks() == [("break_finished", False), ("state", "idle")]
    engine.skip_break()  # no-op
    assert rec.without_ticks() == [("break_finished", False), ("state", "idle")]


def test_start_from_break_ends_it_first(engine: FocusEngine) -> None:
    engine.start_break(5)
    rec = Recorder(engine)
    sid = engine.start("Next block", 25)
    assert rec.without_ticks() == [("break_finished", False), ("started", sid), ("state", "focus")]
    assert engine.state is EngineState.FOCUS


# ================================================================== gaps & clock changes


def test_suspend_gap_pauses_and_reports_away(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    rec = Recorder(engine)
    engine.start("Task", 60)
    run(engine, fake_clock, 600, step=1)
    rec.clear()
    fake_clock.advance(2 * 3600)
    engine.poll()
    assert engine.state is EngineState.PAUSED
    assert rec.without_ticks() == [("state", "paused"), ("away", 7200)]
    assert engine.focused_seconds == pytest.approx(600)
    assert engine.paused_since == datetime(2026, 10, 14, 10, 10)
    assert repo.kv_get("engine_snapshot")["state"] == "paused"
    engine.resume()
    run(engine, fake_clock, 30)
    assert engine.focused_seconds == pytest.approx(630)


def test_small_gap_is_counted(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start("Task", 60)
    fake_clock.advance(85)
    engine.poll()
    assert engine.state is EngineState.FOCUS
    assert engine.focused_seconds == pytest.approx(85)


def test_gap_past_break_end_finishes_silently(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start_break(5)
    rec = Recorder(engine)
    fake_clock.advance(3600)
    engine.poll()
    assert rec.without_ticks() == [("break_finished", False), ("state", "idle")]


def test_gap_inside_break_keeps_break(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start_break(30)
    fake_clock.advance(600)
    engine.poll()
    assert engine.state is EngineState.BREAK
    assert engine.break_remaining_whole_seconds == 1200


def test_gap_while_paused_is_harmless(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start("Task", 60)
    run(engine, fake_clock, 120)
    engine.pause()
    rec = Recorder(engine)
    fake_clock.advance(5 * 3600)
    engine.poll()
    assert rec.without_ticks() == []
    assert engine.state is EngineState.PAUSED


def test_clock_set_back(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    sid = engine.start("Task", 25)
    run(engine, fake_clock, 300, step=10)
    fake_clock.advance(-600)  # user/NTP sets the clock back 10 minutes
    engine.poll()
    assert engine.state is EngineState.FOCUS
    assert engine.focused_seconds == pytest.approx(300)
    run(engine, fake_clock, 600, step=10)
    assert engine.focused_seconds == pytest.approx(900)
    run(engine, fake_clock, 600, step=10)
    assert engine.state is EngineState.IDLE
    row = repo.get_session(sid)
    assert row.outcome is Outcome.COMPLETED and row.focused_seconds == 1500


def test_clock_set_back_while_paused_and_in_break(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start("Task", 25)
    run(engine, fake_clock, 60)
    engine.pause()
    run(engine, fake_clock, 60)
    fake_clock.advance(-600)
    engine.poll()
    assert engine.paused_seconds == pytest.approx(60)
    engine.abandon()
    engine.start_break(5)
    run(engine, fake_clock, 60)
    fake_clock.advance(-600)
    engine.poll()
    assert engine.break_remaining_whole_seconds == 240


@pytest.mark.skipif(NY is None, reason="zoneinfo America/New_York unavailable")
def test_dst_spring_forward(qapp, repo, settings_store) -> None:
    clock = FakeClock(datetime(2026, 3, 8, 1, 30), tz=NY)
    repo.clock = clock
    engine = FocusEngine(repo, settings_store, clock)
    sid = engine.start("Across DST", 60)
    run(engine, clock, 1800, step=30)
    assert clock.now() == datetime(2026, 3, 8, 3, 0)  # wall clock jumped
    assert engine.remaining_whole_seconds == 1800
    assert engine.ends_at == datetime(2026, 3, 8, 3, 30)
    run(engine, clock, 1800, step=30)
    assert engine.state is EngineState.IDLE
    row = repo.get_session(sid)
    assert row.focused_seconds == 3600
    assert row.started_at == datetime(2026, 3, 8, 1, 30)
    assert row.ended_at == datetime(2026, 3, 8, 3, 30)
    engine.stop_timer()


@pytest.mark.skipif(NY is None, reason="zoneinfo America/New_York unavailable")
def test_dst_fall_back(qapp, repo, settings_store) -> None:
    clock = FakeClock(datetime(2026, 11, 1, 1, 15), tz=NY)
    repo.clock = clock
    engine = FocusEngine(repo, settings_store, clock)
    rec = Recorder(engine)
    sid = engine.start("Across DST", 90)
    run(engine, clock, 3600, step=30)
    assert clock.now() == datetime(2026, 11, 1, 1, 15)  # wall clock repeated an hour
    assert engine.state is EngineState.FOCUS  # wall time going "back" is not a clock set-back
    assert engine.focused_seconds == pytest.approx(3600)
    run(engine, clock, 1800, step=30)
    assert ("away", 3600) not in rec.events
    row = repo.get_session(sid)
    assert row.outcome is Outcome.COMPLETED
    assert row.focused_seconds == 5400
    assert row.ended_at == datetime(2026, 11, 1, 1, 45)
    engine.stop_timer()


# ================================================================== persistence


def test_heartbeat_checkpoint_every_30s(engine: FocusEngine, repo, fake_clock: FakeClock) -> None:
    sid = engine.start("Task", 25)
    t0 = fake_clock.time()
    run(engine, fake_clock, 29)
    assert repo.get_session(sid).focused_seconds == 0
    assert repo.kv_get("engine_snapshot")["last_alive"] == pytest.approx(t0)
    run(engine, fake_clock, 1)
    assert repo.get_session(sid).focused_seconds == 30
    assert repo.kv_get("engine_snapshot")["last_alive"] == pytest.approx(t0 + 30)
    run(engine, fake_clock, 31)
    assert repo.get_session(sid).focused_seconds == 60


def test_checkpoint_is_transactional(engine: FocusEngine, repo, fake_clock, monkeypatch) -> None:
    sid = engine.start("Task", 25)
    run(engine, fake_clock, 40)
    before = repo.kv_get("engine_snapshot")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repo, "update_session", boom)
    run(engine, fake_clock, 40)  # heartbeat fails → logged, engine keeps running
    engine.checkpoint()
    assert repo.kv_get("engine_snapshot") == before  # snapshot write rolled back with the row update
    assert engine.state is EngineState.FOCUS
    monkeypatch.undo()
    engine.checkpoint()
    assert repo.kv_get("engine_snapshot")["last_alive"] > before["last_alive"]
    assert repo.get_session(sid).focused_seconds == 80


def test_start_rolls_back_when_db_fails(engine: FocusEngine, repo, monkeypatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "save_engine_checkpoint", boom)
    assert engine.start("Task", 25) is None
    assert engine.state is EngineState.IDLE
    assert repo.active_sessions() == []


def test_idle_checkpoint_clears_snapshot(engine: FocusEngine, repo) -> None:
    repo.kv_set("engine_snapshot", {"v": 1, "state": "focus"})
    engine.checkpoint()
    assert repo.kv_get("engine_snapshot") is None


# ================================================================== ticks & timer


def test_tick_only_on_whole_second_change(engine: FocusEngine, fake_clock: FakeClock) -> None:
    engine.start("Task", 25)
    rec = Recorder(engine)
    for _ in range(3):  # 1499.1 s left still displays 1500
        fake_clock.advance(0.3)
        engine.poll()
    assert rec.events == []
    fake_clock.advance(0.2)
    engine.poll()
    assert rec.events == [("tick",)]  # 1500 → 1499 exactly once
    assert engine.remaining_whole_seconds == 1499
    fake_clock.advance(0.5)
    engine.poll()
    assert rec.events == [("tick",)]
    fake_clock.advance(1.0)
    engine.poll()
    assert rec.events == [("tick",), ("tick",)]


def test_timer_is_armed_to_next_second_boundary(engine: FocusEngine, fake_clock: FakeClock) -> None:
    assert not engine._timer.isActive()
    engine.start("Task", 25)
    assert engine._timer.isActive()
    assert engine._timer.interval() == 1000
    fake_clock.advance(0.3)
    engine.poll()
    assert engine._timer.interval() == pytest.approx(720, abs=2)
    engine.pause()
    assert 1000 <= engine._timer.interval() <= 30_000
    engine.abandon()
    assert not engine._timer.isActive()


def test_timer_drives_poll_in_real_time(qtbot, repo, settings_store) -> None:
    from madhura.core.clock import SystemClock

    engine = FocusEngine(repo, settings_store, SystemClock())
    engine.start("Real", 1)
    ticks: list[int] = []
    engine.tick.connect(lambda: ticks.append(engine.remaining_whole_seconds))
    qtbot.waitUntil(lambda: len(ticks) >= 1, timeout=2500)
    assert ticks[0] == 59
    engine.abandon()


def test_completed_sessions_today_uses_logical_day(engine: FocusEngine, repo, settings_store, fake_clock) -> None:
    def add(start: datetime, outcome: str) -> None:
        sid = repo.create_session("x", None, None, start, 60)
        repo.update_session(sid, outcome=outcome, focused_seconds=60)

    add(datetime(2026, 10, 14, 4, 30), "completed")
    add(datetime(2026, 10, 14, 9, 0), "completed")
    add(datetime(2026, 10, 14, 9, 30), "ended_early")
    add(datetime(2026, 10, 14, 3, 0), "completed")  # belongs to the 13th with day_start_hour=4
    assert engine.completed_sessions_today == 2
    settings_store.update(day_start_hour=0)
    assert engine.completed_sessions_today == 3
    fake_clock.set(datetime(2026, 10, 15, 2, 0))
    settings_store.update(day_start_hour=4)
    assert engine.completed_sessions_today == 2  # still logically the 14th
