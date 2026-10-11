"""[O] AwayService + AwayPanel (spec §1.5: idle ≥ 300 s during FOCUS → keep / remove)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication
from test_o_support import advance, octx, set_idle  # noqa: F401

from madhura.context import AppContext
from madhura.ui.overlays.away_panel import AwayPanel, away_label
from madhura.ui.widgets.popup import PopupStack


class Idle:
    """A controllable input-idle clock: idle grows with the FakeClock until touched."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self.last_input = ctx.clock.time()
        self.enabled = True

    def touch(self) -> None:
        self.last_input = self._ctx.clock.time()

    def __call__(self) -> float | None:
        if not self.enabled:
            return None
        return self._ctx.clock.time() - self.last_input


@pytest.fixture
def idle(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> Idle:  # noqa: F811
    fn = Idle(octx)
    monkeypatch.setattr(octx.attention, "input_idle_seconds", fn)
    return fn


def work(ctx: AppContext, idle: Idle, seconds: float, step: float = 10.0) -> None:
    """At the keyboard for *seconds* (input every step)."""
    remaining = float(seconds)
    while remaining > 1e-9:
        d = min(step, remaining)
        idle.touch()
        advance(ctx, d, d)
        remaining -= d
    idle.touch()


def go_away(ctx: AppContext, idle: Idle, seconds: float) -> None:
    """Work for a bit, then leave the keyboard for *seconds* and come back."""
    idle.touch()
    advance(ctx, seconds, 10)
    idle.touch()
    advance(ctx, 1, 1)


def test_away_label() -> None:
    assert away_label(14 * 60 + 30) == "14 min"
    assert away_label(10) == "1 min"
    assert away_label(72 * 60) == "1h 12m"


def test_short_idle_is_ignored(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    go_away(octx, idle, 290)
    assert octx.away.panel is None


def test_return_after_away_offers_removal(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    returned: list[int] = []
    octx.away.away_returned.connect(returned.append)
    octx.engine.start("Write", 60)
    advance(octx, 120)
    go_away(octx, idle, 14 * 60)
    panel = octx.away.panel
    assert isinstance(panel, AwayPanel)
    assert PopupStack.instance().is_visible_panel(panel)
    assert returned and returned[0] == pytest.approx(14 * 60, abs=2)
    assert panel.title.text() == "Away for 14 min"
    assert panel.remove_button.text() == "Remove 14 min"
    before = octx.engine.focused_seconds
    panel.remove_button.click()
    assert panel.decision() == "remove"
    assert octx.engine.focused_seconds == pytest.approx(before - 14 * 60, abs=2)
    assert "Removed 14 min" in panel.confirm_label.text()


def test_keep_leaves_the_time(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    go_away(octx, idle, 10 * 60)
    panel = octx.away.panel
    before = octx.engine.focused_seconds
    panel.keep_button.click()
    assert panel.decision() == "keep"
    assert octx.engine.focused_seconds == pytest.approx(before)
    QApplication.processEvents()


def test_no_answer_keeps(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    go_away(octx, idle, 10 * 60)
    panel = octx.away.panel
    before = octx.engine.focused_seconds
    assert panel.countdown.is_running() and panel.countdown.duration_ms == 60_000
    panel.hover_changed.emit(True)
    assert panel.countdown.is_paused()
    panel.hover_changed.emit(False)
    assert panel.countdown.remaining_ms() >= 10_000
    panel.countdown.expire_now()
    assert panel.decision() == "keep"
    assert octx.engine.focused_seconds == pytest.approx(before)


def test_offer_is_capped_by_focused_time(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    idle.touch()
    advance(octx, 200)  # idle before the session starts
    octx.engine.start("Write", 60)
    advance(octx, 400, 10)  # idle 600 s in total, 400 s of it focused
    idle.touch()
    advance(octx, 1, 1)
    panel = octx.away.panel
    assert panel is not None
    assert panel.seconds() == pytest.approx(401, abs=2)


def test_panel_outlives_its_session_and_corrects_the_saved_row(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    changed: list[str] = []
    octx.events.data_changed.connect(changed.append)
    sid = octx.engine.start("Write", 60)
    work(octx, idle, 10 * 60)
    go_away(octx, idle, 6 * 60)
    panel = octx.away.panel
    assert panel is not None and octx.away.panel_session_id == sid
    octx.engine.finish()
    assert panel.decision() is None and octx.away.panel is panel  # still asking
    row = octx.repo.get_session(sid)
    assert str(row.outcome) == "ended_early"
    focused, paused = row.focused_seconds, row.paused_seconds
    changed.clear()
    panel.remove_button.click()
    assert panel.decision() == "remove"
    row = octx.repo.get_session(sid)
    assert row.focused_seconds == pytest.approx(focused - 6 * 60, abs=2)
    assert row.paused_seconds == pytest.approx(paused + 6 * 60, abs=2)
    assert "sessions" in changed
    assert "Removed 6 min" in panel.confirm_label.text()


def test_abandon_or_a_new_session_cancels_the_panel(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    work(octx, idle, 120)
    go_away(octx, idle, 6 * 60)
    panel = octx.away.panel
    octx.engine.abandon()
    assert panel.decision() == "cancel"
    octx.engine.start("Read", 60)
    work(octx, idle, 120)
    go_away(octx, idle, 6 * 60)
    panel = octx.away.panel
    octx.engine.finish()
    octx.engine.start("Next", 30)
    assert panel.decision() == "cancel"


def test_session_completing_just_after_the_return_keeps_the_panel(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    sid = octx.engine.start("Write", 25)
    work(octx, idle, 5 * 60)
    go_away(octx, idle, 19 * 60)  # back with ~1 min left
    panel = octx.away.panel
    assert panel is not None
    advance(octx, 90, 1)  # the block runs out while the panel is still undecided
    assert str(octx.engine.state) == "idle"
    assert str(octx.repo.get_session(sid).outcome) == "completed"
    assert octx.away.panel is panel and panel.decision() is None
    panel.remove_button.click()
    row = octx.repo.get_session(sid)
    assert row.focused_seconds == pytest.approx(25 * 60 - 19 * 60, abs=3)
    assert str(row.outcome) == "completed"


def test_reaching_the_end_while_away_pauses_where_the_user_left(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    """Windows/macOS, lock the PC and leave: the block must not complete with the away time in it."""
    away: list[int] = []
    octx.engine.away_detected.connect(away.append)
    finished: list[str] = []
    octx.engine.session_finished.connect(lambda _sid, outcome, _r: finished.append(outcome))
    sid = octx.engine.start("Write", 25)
    work(octx, idle, 5 * 60)  # last input at 5:00
    advance(octx, 30 * 60, 1)
    engine = octx.engine
    assert finished == [] and str(engine.state) == "paused" and engine.session_id == sid
    assert engine.focused_seconds == pytest.approx(5 * 60, abs=2)
    assert engine.remaining_seconds == pytest.approx(20 * 60, abs=2)
    assert away and away[0] == pytest.approx(20 * 60, abs=3)  # away until the planned end
    assert octx.repo.get_session(sid).focused_seconds == pytest.approx(5 * 60, abs=2)
    assert octx.away.paused_while_away
    assert octx.pill.pill.time_text() == "Paused · were you away?"
    idle.touch()  # back: nothing left to remove, so no panel
    advance(octx, 2, 1)
    assert octx.away.panel is None
    engine.resume()
    assert not octx.away.paused_while_away
    assert octx.pill.pill.time_text() != "Paused · were you away?"


def test_long_block_away_after_answering_checkins_is_not_credited(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    """The review probe: 60-min block, 20 min of work answering "Focused", then 60 min away."""
    ck = octx.checkins
    sid = octx.engine.start("Write", 60)
    for _ in range(20 * 6):
        work(octx, idle, 10)
        if ck.prompt_mode is not None:
            ck.answer("focused")
    assert ck.counts()["focused"] == 2
    advance(octx, 60 * 60, 5)
    engine = octx.engine
    assert str(engine.state) == "paused" and engine.session_id == sid
    assert engine.focused_seconds == pytest.approx(20 * 60, abs=10)
    engine.finish()
    row = octx.repo.get_session(sid)
    assert str(row.outcome) == "ended_early"
    assert row.focused_seconds == pytest.approx(20 * 60, abs=10)


def test_completion_without_an_away_stretch_is_normal(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    sid = octx.engine.start("Write", 10)
    work(octx, idle, 11 * 60)
    assert str(octx.engine.state) == "idle"
    assert str(octx.repo.get_session(sid).outcome) == "completed"


def test_sleep_during_an_idle_stretch_pauses_at_its_start(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    away: list[int] = []
    octx.engine.away_detected.connect(away.append)
    sid = octx.engine.start("Write", 60)
    work(octx, idle, 5 * 60)  # last input at 5:00; the OS puts the machine to sleep after 20 idle minutes
    advance(octx, 20 * 60, 1)
    assert octx.away.is_away()
    octx.clock.advance(2 * 3600)  # type: ignore[attr-defined]
    octx.engine.poll()
    engine = octx.engine
    assert str(engine.state) == "paused" and engine.session_id == sid
    assert engine.focused_seconds == pytest.approx(5 * 60, abs=2)
    assert away == [pytest.approx(2 * 3600 + 20 * 60, abs=3)]
    assert octx.repo.get_session(sid).focused_seconds == pytest.approx(5 * 60, abs=2)
    idle.touch()
    advance(octx, 2, 1)
    assert octx.away.panel is None


def test_sleep_without_an_idle_stretch_still_pauses_at_the_last_poll(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    work(octx, idle, 10 * 60)
    octx.clock.advance(3600)  # type: ignore[attr-defined]
    octx.engine.poll()
    assert str(octx.engine.state) == "paused"
    assert octx.engine.focused_seconds == pytest.approx(10 * 60, abs=1)
    assert not octx.away.paused_while_away


def test_removing_away_time_restarts_the_checkin_schedule(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    ck = octx.checkins
    octx.engine.start("Write", 60)
    work(octx, idle, 3 * 60)
    go_away(octx, idle, 14 * 60)  # the check-ins due meanwhile were suppressed (no record)
    panel = octx.away.panel
    panel.remove_button.click()
    assert octx.engine.focused_seconds == pytest.approx(3 * 60, abs=3)
    assert ck.seconds_until_next() == pytest.approx(7 * 60, abs=1)


def test_pause_during_idle_forgets_the_stretch(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    idle.touch()
    advance(octx, 400)
    assert octx.away.is_away()
    octx.engine.pause()
    assert not octx.away.is_away()
    octx.engine.resume()
    idle.touch()
    advance(octx, 1, 1)
    assert octx.away.panel is None


def test_unknown_idle_does_nothing(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    idle.enabled = False
    octx.engine.start("Write", 60)
    advance(octx, 900)
    assert octx.away.panel is None and not octx.away.is_away()


def test_second_away_stretch_adds_to_an_open_panel(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 120)
    go_away(octx, idle, 6 * 60)
    panel = octx.away.panel
    go_away(octx, idle, 7 * 60)
    assert octx.away.panel is panel
    assert panel.seconds() == pytest.approx(13 * 60, abs=3)


def test_a_broken_away_probe_never_stops_the_engine(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    def broken() -> float:
        raise RuntimeError("probe failed")

    octx.engine.set_away_probe(broken)
    sid = octx.engine.start("Write", 5)
    work(octx, idle, 6 * 60)
    assert str(octx.engine.state) == "idle"
    assert str(octx.repo.get_session(sid).outcome) == "completed"
    octx.engine.set_away_probe(octx.away.away_since)
