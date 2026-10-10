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


def test_session_end_cancels_the_panel(octx: AppContext, idle: Idle) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    go_away(octx, idle, 6 * 60)
    panel = octx.away.panel
    octx.engine.finish()
    assert panel.decision() == "cancel"


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
