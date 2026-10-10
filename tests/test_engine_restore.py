from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from madhura.core.clock import FakeClock
from madhura.core.models import Outcome
from madhura.services.engine import EngineState, FocusEngine


class Recorder:
    """Records engine signals in emission order."""

    def __init__(self, engine: FocusEngine) -> None:
        self.events: list[tuple[Any, ...]] = []
        engine.state_changed.connect(lambda s: self.events.append(("state", s)))
        engine.tick.connect(lambda: self.events.append(("tick",)))
        engine.session_started.connect(lambda i: self.events.append(("started", i)))
        engine.session_finished.connect(lambda i, o, r: self.events.append(("finished", i, o, r)))
        engine.break_started.connect(lambda s: self.events.append(("break_started", s)))
        engine.break_finished.connect(lambda n: self.events.append(("break_finished", n)))
        engine.away_detected.connect(lambda s: self.events.append(("away", s)))

    def without_ticks(self) -> list[tuple[Any, ...]]:
        return [e for e in self.events if e[0] != "tick"]


def run(engine: FocusEngine, clock: FakeClock, seconds: float, step: float = 1.0) -> None:
    """Advance the fake clock in live-sized steps, polling like the timer would."""
    remaining = seconds
    while remaining > 1e-9:
        delta = min(step, remaining)
        clock.advance(delta)
        engine.poll()
        remaining -= delta


@pytest.fixture
def fresh(qapp, repo, settings_store, fake_clock):
    """Factory for a second engine instance (simulates an app restart on the same database)."""
    made: list[FocusEngine] = []

    def make() -> FocusEngine:
        engine = FocusEngine(repo, settings_store, fake_clock)
        made.append(engine)
        return engine

    yield make
    for engine in made:
        engine.stop_timer()


def crash_during_focus(engine: FocusEngine, clock: FakeClock, minutes: int = 25, run_for: float = 600) -> int:
    sid = engine.start("Task", minutes, project="P", planned_task_id=9)
    run(engine, clock, run_for, step=1)
    engine.stop_timer()  # the process dies: no more polls or checkpoints
    return sid


def test_restore_without_snapshot_is_idle(fresh) -> None:
    engine = fresh()
    rec = Recorder(engine)
    engine.restore()
    assert engine.state is EngineState.IDLE
    assert rec.events == []


def test_restore_short_gap_resumes_focus_and_counts_gap(engine, fresh, repo, fake_clock) -> None:
    sid = crash_during_focus(engine, fake_clock)  # last heartbeat at 600 s
    fake_clock.advance(60)
    restored = fresh()
    rec = Recorder(restored)
    restored.restore()
    assert restored.state is EngineState.FOCUS
    assert restored.session_id == sid
    assert (restored.task, restored.project, restored.planned_task_id) == ("Task", "P", 9)
    assert restored.started_at == datetime(2026, 10, 14, 10, 0)
    assert restored.focused_seconds == pytest.approx(660)
    assert rec.without_ticks() == [("state", "focus")]
    assert repo.kv_get("engine_snapshot")["last_alive"] == pytest.approx(fake_clock.time())


def test_restore_long_gap_pauses_with_away(engine, fresh, repo, fake_clock) -> None:
    sid = crash_during_focus(engine, fake_clock)
    last_alive = repo.kv_get("engine_snapshot")["last_alive"]
    fake_clock.advance(3 * 3600)
    restored = fresh()
    rec = Recorder(restored)
    restored.restore()
    assert restored.state is EngineState.PAUSED
    assert restored.session_id == sid
    assert restored.focused_seconds == pytest.approx(600)
    assert restored.paused_since == datetime(2026, 10, 14, 10, 10)
    gap = int(round(fake_clock.time() - last_alive))
    assert rec.without_ticks() == [("state", "paused"), ("away", gap)]
    assert gap == 3 * 3600
    restored.resume()
    run(restored, fake_clock, 900, step=30)
    assert restored.state is EngineState.IDLE
    row = repo.get_session(sid)
    assert row.outcome is Outcome.COMPLETED
    assert row.focused_seconds == 1500
    assert row.paused_seconds == pytest.approx(3 * 3600, abs=1)


def test_restore_session_completed_while_closed(engine, fresh, repo, fake_clock) -> None:
    sid = crash_during_focus(engine, fake_clock, minutes=25, run_for=1490)
    snap = repo.kv_get("engine_snapshot")
    # Pretend the app kept running (heartbeat) right up to the planned end before dying.
    snap["last_alive"] = snap["segment_start"] + 1500 + 5
    repo.kv_set("engine_snapshot", snap)
    fake_clock.advance(4 * 3600)
    restored = fresh()
    rec = Recorder(restored)
    restored.restore()
    assert rec.without_ticks() == [("finished", sid, "completed", True)]
    assert restored.state is EngineState.IDLE
    row = repo.get_session(sid)
    assert row.outcome is Outcome.COMPLETED
    assert row.focused_seconds == 1500
    assert row.ended_at == datetime(2026, 10, 14, 10, 25)
    assert repo.kv_get("engine_snapshot") is None


def test_restore_paused_stays_paused(engine, fresh, fake_clock) -> None:
    engine.start("Task", 25)
    run(engine, fake_clock, 120)
    engine.pause()
    engine.stop_timer()
    fake_clock.advance(5 * 3600)
    restored = fresh()
    rec = Recorder(restored)
    restored.restore()
    assert restored.state is EngineState.PAUSED
    assert restored.focused_seconds == pytest.approx(120)
    assert restored.paused_since == datetime(2026, 10, 14, 10, 2)
    assert rec.without_ticks() == [("state", "paused")]


def test_restore_break_running_and_expired(engine, fresh, repo, fake_clock) -> None:
    engine.start_break(10, "Lunch")
    engine.stop_timer()
    fake_clock.advance(120)
    restored = fresh()
    rec = Recorder(restored)
    restored.restore()
    assert restored.state is EngineState.BREAK
    assert restored.break_label == "Lunch"
    assert restored.break_remaining_whole_seconds == 480
    assert rec.without_ticks() == [("state", "break")]
    restored.stop_timer()

    fake_clock.advance(3600)
    again = fresh()
    rec2 = Recorder(again)
    again.restore()
    assert again.state is EngineState.IDLE
    assert rec2.events == []  # silently
    assert repo.kv_get("engine_snapshot") is None


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        "garbage",
        {"v": 2, "state": "focus"},
        {"v": 1, "state": "dancing", "last_alive": 1.0},
        {"v": 1, "state": "focus", "last_alive": "yesterday"},
        {"v": 1, "state": "focus", "session_id": "1", "planned_seconds": 60, "segment_start": 1.0, "last_alive": 1.0},
        {"v": 1, "state": "focus", "session_id": 1, "planned_seconds": 0, "segment_start": 1.0, "last_alive": 1.0},
        {"v": 1, "state": "focus", "session_id": 1, "planned_seconds": 60, "segment_start": None, "last_alive": 1.0},
        {"v": 1, "state": "paused", "session_id": 1, "planned_seconds": 60, "pause_start": None, "last_alive": 1.0},
        {"v": 1, "state": "break", "break_total": 0, "break_start": 1.0, "last_alive": 1.0},
        {"v": 1, "state": "break", "break_total": 60, "break_start": None, "last_alive": 1.0},
    ],
)
def test_invalid_snapshot_is_discarded(fresh, repo, snapshot: Any) -> None:
    repo.kv_set("engine_snapshot", snapshot)
    engine = fresh()
    engine.restore()
    assert engine.state is EngineState.IDLE
    assert repo.kv_get("engine_snapshot") is None


def test_snapshot_with_missing_or_finished_row_is_discarded(engine, fresh, repo, fake_clock) -> None:
    sid = crash_during_focus(engine, fake_clock, run_for=120)
    snap = repo.kv_get("engine_snapshot")
    repo.update_session(sid, outcome="completed")
    restored = fresh()
    restored.restore()
    assert restored.state is EngineState.IDLE
    assert repo.kv_get("engine_snapshot") is None

    repo.kv_set("engine_snapshot", snap)
    repo.delete_session(sid)
    again = fresh()
    again.restore()
    assert again.state is EngineState.IDLE


def test_orphan_active_rows_are_closed(fresh, repo) -> None:
    start = datetime(2026, 10, 13, 9, 0)
    long_id = repo.create_session("Long orphan", None, None, start, 3600)
    repo.update_session(long_id, focused_seconds=1200)
    short_id = repo.create_session("Short orphan", None, None, start, 3600)
    repo.update_session(short_id, focused_seconds=59)
    engine = fresh()
    engine.restore()
    row = repo.get_session(long_id)
    assert row.outcome is Outcome.ENDED_EARLY
    assert row.ended_at == start + timedelta(seconds=1200)
    assert repo.get_session(short_id) is None
    assert repo.active_sessions() == []


def test_orphans_closed_but_snapshot_session_kept(engine, fresh, repo, fake_clock) -> None:
    orphan = repo.create_session("Orphan", None, None, datetime(2026, 10, 14, 8, 0), 1500)
    repo.update_session(orphan, focused_seconds=300)
    sid = crash_during_focus(engine, fake_clock, run_for=120)
    fake_clock.advance(30)
    restored = fresh()
    restored.restore()
    assert restored.session_id == sid
    assert restored.state is EngineState.FOCUS
    assert repo.get_session(orphan).outcome is Outcome.ENDED_EARLY
    assert [s.id for s in repo.active_sessions()] == [sid]


def test_restore_after_clock_set_back(engine, fresh, fake_clock) -> None:
    crash_during_focus(engine, fake_clock, run_for=300)
    fake_clock.advance(-3600)  # clock moved back while closed
    restored = fresh()
    restored.restore()
    assert restored.state is EngineState.FOCUS
    assert restored.focused_seconds == pytest.approx(300)
    run(restored, fake_clock, 60)
    assert restored.focused_seconds == pytest.approx(360)


def test_restore_twice_is_ignored(engine, fresh, fake_clock) -> None:
    crash_during_focus(engine, fake_clock, run_for=60)
    restored = fresh()
    restored.restore()
    rec = Recorder(restored)
    restored.restore()
    assert rec.events == []
