"""[O] CheckInService + CheckInPopup (spec §1.5, §5.3)."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QApplication
from test_o_support import (  # noqa: F401
    advance,
    dispose_octx,
    make_octx,
    octx,
    records,
    set_attention,
    set_idle,
)

from madhura.context import AppContext, FakeWindowController
from madhura.services.checkin_service import INTRO_KEY, REASON_FALLBACK_SECONDS
from madhura.ui.overlays.checkin_popup import CheckInPopup, confirmation_text, meta_text
from madhura.ui.widgets.popup import PopupStack

BASE = 7 * 60


def start(ctx: AppContext, minutes: int = 60, task: str = "Write the report") -> int:
    sid = ctx.engine.start(task, minutes)
    assert sid is not None
    return sid


def run_to_prompt(ctx: AppContext, limit: float = 3600, step: float = 5.0) -> float:
    """Advance until a prompt opens; returns the focused seconds at that moment."""
    ck = ctx.checkins
    assert ck is not None
    elapsed = 0.0
    while ck.prompt_mode is None:
        advance(ctx, step, step)
        elapsed += step
        assert elapsed <= limit, "no check-in fired"
    return ctx.engine.focused_seconds


# --------------------------------------------------------------------------- pure helpers


def test_meta_and_confirmation_copy() -> None:
    assert meta_text(23 * 60 + 40, 21 * 60 + 5) == "23 min in · 22 left"
    assert meta_text(0, 0) == "0 min in · 0 left"
    assert confirmation_text("focused", "Write", 420, 1800) == "Nice — next check-in in ~7 min"
    # the next one could not fire before the block ends → no promise
    assert confirmation_text("focused", "Write", 420, 450) == "Nice — keep going"
    assert confirmation_text("drifted", "Write", 420, 1800) == "Back to Write."
    assert confirmation_text("drifted", "", None, 0) == "Back to your task."


# --------------------------------------------------------------------------- timing


def test_fires_after_one_interval_of_focused_time(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    assert ck.seconds_until_next() == pytest.approx(BASE)
    advance(octx, BASE - 10)
    assert ck.prompt_mode is None
    advance(octx, 10)
    assert ck.prompt_mode == "popup"
    assert isinstance(ck.popup, CheckInPopup)
    assert PopupStack.instance().is_visible_panel(ck.popup)
    assert records(octx) == []  # nothing recorded until answered


def test_paused_time_does_not_count(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    advance(octx, 180)
    octx.engine.pause()
    advance(octx, 900, 30)
    assert ck.prompt_mode is None
    assert ck.seconds_until_next() is None  # not focusing
    octx.engine.resume()
    advance(octx, 230)
    assert ck.prompt_mode is None
    fired_at = run_to_prompt(octx)
    assert fired_at == pytest.approx(BASE, abs=6)


def test_no_checkin_in_the_last_minute(octx: AppContext) -> None:  # noqa: F811
    start(octx, minutes=8)  # due at 7:00 with 60 s left → still fires (< 60 s does not)
    advance(octx, BASE)
    assert octx.checkins.prompt_mode == "popup"
    octx.checkins.answer("focused")
    octx.engine.abandon()
    start(octx, minutes=7)
    advance(octx, BASE - 5)
    assert octx.checkins.prompt_mode is None  # < 60 s remain at the due time


def test_adaptive_spacing_and_reset(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx, minutes=240)
    fires: list[float] = []
    for _ in range(4):
        fires.append(run_to_prompt(octx))
        ck.answer("focused")
    gaps = [b - a for a, b in zip(fires, fires[1:], strict=False)]
    assert fires[0] == pytest.approx(BASE, abs=6)
    assert gaps[0] == pytest.approx(BASE, abs=6)
    assert gaps[1] == pytest.approx(BASE, abs=6)
    assert gaps[2] == pytest.approx(BASE * 1.5, abs=6)  # after 3 focused answers
    for _ in range(3):
        fires.append(run_to_prompt(octx))
        ck.answer("focused")
    assert fires[-1] - fires[-2] == pytest.approx(BASE * 2, abs=6)  # after 6 focused
    fires.append(run_to_prompt(octx))
    ck.answer("drifted", "Phone")
    fires.append(run_to_prompt(octx))
    assert fires[-1] - fires[-2] == pytest.approx(BASE, abs=6)  # drift resets
    ck.answer("focused")


def test_adaptive_off_keeps_base(octx: AppContext) -> None:  # noqa: F811
    octx.settings.update(checkin_adaptive=False)
    ck = octx.checkins
    start(octx, minutes=240)
    fires = []
    for _ in range(5):
        fires.append(run_to_prompt(octx))
        ck.answer("focused")
    assert all(b - a == pytest.approx(BASE, abs=6) for a, b in zip(fires, fires[1:], strict=False))


def test_settings_change_reconfigures_interval(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    advance(octx, 60)
    octx.settings.update(checkin_interval_minutes=15)
    assert ck.seconds_until_next() == pytest.approx(15 * 60 - 60, abs=1)
    octx.settings.update(checkin_enabled=False)
    assert ck.seconds_until_next() is None
    advance(octx, 20 * 60, 30)
    assert ck.prompt_mode is None


def test_disabled_closes_an_open_prompt(octx: AppContext) -> None:  # noqa: F811
    start(octx)
    run_to_prompt(octx)
    octx.settings.update(checkin_enabled=False)
    assert octx.checkins.prompt_mode is None
    assert records(octx) == []


# --------------------------------------------------------------------------- routing


def test_inline_when_main_window_active_on_focus_page(qapp: QApplication, data_dir: Any, fake_clock: Any) -> None:
    win = FakeWindowController(page="focus", visible=True, active=True)
    ctx = make_octx(qapp, data_dir, fake_clock, window=win)
    try:
        ck = ctx.checkins
        seen: list[str] = []
        ck.prompt_inline.connect(lambda: seen.append("inline"))
        answered: list[str] = []
        ck.answered.connect(answered.append)
        changed: list[str] = []
        ctx.events.data_changed.connect(changed.append)
        start(ctx)
        run_to_prompt(ctx)
        assert seen == ["inline"]
        assert ck.prompt_mode == "inline" and ck.popup is None
        assert ck.timeout_progress() == pytest.approx(1.0)
        advance(ctx, 30)
        assert ck.timeout_progress() == pytest.approx(0.5, abs=0.01)
        ck.answer("focused")
        assert ck.prompt_mode is None and ck.timeout_progress() is None
        assert records(ctx) == [("focused", None)]
        assert answered == ["focused"] and "checkins" in changed
        assert ck.counts() == {"focused": 1, "drifted": 0, "missed": 0}
    finally:
        dispose_octx(ctx, qapp)


@pytest.mark.parametrize("page,sheet", [("habits", False), ("focus", True)])
def test_popup_when_not_on_focus_page_or_sheet_open(
    qapp: QApplication, data_dir: Any, fake_clock: Any, page: str, sheet: bool
) -> None:
    win = FakeWindowController(page=page, visible=True, active=True)
    if sheet:
        win.sheets.append(object())
    ctx = make_octx(qapp, data_dir, fake_clock, window=win)
    try:
        start(ctx)
        run_to_prompt(ctx)
        assert ctx.checkins.prompt_mode == "popup"
    finally:
        dispose_octx(ctx, qapp)


def test_inline_prompt_moves_to_popup_when_window_goes_away(qapp: QApplication, data_dir: Any, fake_clock: Any) -> None:
    win = FakeWindowController(page="focus", visible=True, active=True)
    ctx = make_octx(qapp, data_dir, fake_clock, window=win)
    try:
        ck = ctx.checkins
        closed: list[int] = []
        ck.prompt_closed.connect(lambda: closed.append(1))
        start(ctx)
        run_to_prompt(ctx)
        assert ck.prompt_mode == "inline"
        advance(ctx, 20)
        win.active = False
        advance(ctx, 1, 1)
        assert closed == [1]
        assert ck.prompt_mode == "popup" and ck.popup is not None
        # the same prompt: the remaining time carries over (≈ 39 s of 60)
        assert ck.seconds_left() == pytest.approx(39, abs=1.5)
        assert records(ctx) == []
    finally:
        dispose_octx(ctx, qapp)


# --------------------------------------------------------------------------- answers


def test_popup_focused_answer_records_and_confirms(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    answered: list[str] = []
    ck.answered.connect(answered.append)
    start(octx)
    run_to_prompt(octx)
    popup = ck.popup
    assert popup is not None and popup.phase() == "question"
    assert popup.title.text() == "Still on Write the report?"
    assert popup.meta.text() == "7 min in · 53 left"
    popup.focused_button.click()
    assert popup.phase() == "confirm"
    assert popup.confirmation() == "Nice — next check-in in ~7 min"
    assert records(octx) == [("focused", None)]
    assert answered == ["focused"]
    assert ck.prompt_mode is None
    assert ck.popup is popup  # still showing its 1.2 s confirmation
    popup._confirm_timer.timeout.emit()  # noqa: SLF001 - skip the 1.2 s wait
    QApplication.processEvents()
    assert not PopupStack.instance().contains(popup)


def test_popup_focusing_back_with_reason(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    run_to_prompt(octx)
    popup = ck.popup
    popup.back_button.click()
    assert popup.phase() == "reason"
    assert popup.reasons.isVisibleTo(popup)
    # the answer is decided: the prompt timeout no longer runs (hovering holds the reason step)
    popup.hover_changed.emit(True)
    advance(octx, 120)
    assert records(octx) == []
    popup.choose_reason("Messages")
    assert records(octx) == [("drifted", "Messages")]
    assert popup.confirmation() == "Back to Write the report."


def test_reason_step_that_never_answers_still_records_drifted(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    run_to_prompt(octx)
    popup = ck.popup
    popup.answer_back()
    popup._reason_countdown.stop()  # noqa: SLF001 - as if the popup's 6 s timer were lost
    assert ck.timeout_progress() == pytest.approx(1.0)
    advance(octx, REASON_FALLBACK_SECONDS - 2, 1)
    assert records(octx) == [] and ck.prompt_mode == "popup"
    advance(octx, 3, 1)
    assert records(octx) == [("drifted", None)]
    assert ck.prompt_mode is None
    run_to_prompt(octx)  # the schedule goes on
    assert ck.prompt_mode == "popup"


def test_inline_focusing_back_then_leaving_the_window_records_drifted(
    qapp: QApplication, data_dir: Any, fake_clock: Any
) -> None:
    win = FakeWindowController(page="focus", visible=True, active=True)
    ctx = make_octx(qapp, data_dir, fake_clock, window=win)
    try:
        ck = ctx.checkins
        answered: list[str] = []
        ck.answered.connect(answered.append)
        start(ctx)
        run_to_prompt(ctx)
        assert ck.prompt_mode == "inline"
        ck.begin_reason()  # "Focusing back" on the Focus page strip
        win.active = False  # ... and the user switches to their work app within the 6 s
        advance(ctx, 2, 1)
        assert records(ctx) == [("drifted", None)] and answered == ["drifted"]
        assert ck.prompt_mode is None and ck.popup is None  # not asked again in a popup
        run_to_prompt(ctx)  # later check-ins still fire
        assert ck.prompt_mode == "popup"
    finally:
        dispose_octx(ctx, qapp)


def test_reason_step_times_out_to_drifted_without_reason(octx: AppContext) -> None:  # noqa: F811
    start(octx)
    run_to_prompt(octx)
    popup = octx.checkins.popup
    popup.answer_back()
    popup._reason_countdown.expire_now()  # noqa: SLF001 - the 6 s auto-close
    assert records(octx) == [("drifted", None)]
    assert popup.phase() == "confirm"


def test_reason_skip_link(octx: AppContext) -> None:  # noqa: F811
    start(octx)
    run_to_prompt(octx)
    popup = octx.checkins.popup
    popup.answer_back()
    popup.skip_link.click()
    assert records(octx) == [("drifted", None)]


def test_first_checkin_ever_shows_the_intro_once(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    assert not octx.repo.kv_get(INTRO_KEY)
    start(octx)
    run_to_prompt(octx)
    assert ck.popup.intro_shown()
    assert octx.repo.kv_get(INTRO_KEY) is True
    ck.popup.answer_focused()
    run_to_prompt(octx)
    assert not ck.popup.intro_shown()


def test_unknown_response_is_ignored(octx: AppContext) -> None:  # noqa: F811
    start(octx)
    octx.checkins.answer("maybe")
    assert records(octx) == []


# --------------------------------------------------------------------------- timeout / misses


def test_timeout_records_missed(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    answered: list[str] = []
    ck.answered.connect(answered.append)
    start(octx)
    run_to_prompt(octx)
    popup = ck.popup
    advance(octx, 55)
    assert records(octx) == []
    advance(octx, 10)
    assert records(octx) == [("missed", None)]
    assert answered == ["missed"]
    assert ck.prompt_mode is None and ck.popup is None
    assert not PopupStack.instance().is_visible_panel(popup)


def test_hover_pauses_the_timeout_and_resumes_with_ten_seconds(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    run_to_prompt(octx)
    advance(octx, 55)
    ck.popup.hover_changed.emit(True)
    advance(octx, 300, 30)
    assert records(octx) == []
    assert ck.seconds_left() == pytest.approx(5, abs=1)
    ck.popup.hover_changed.emit(False)
    assert ck.seconds_left() == pytest.approx(10, abs=0.01)
    advance(octx, 9)
    assert records(octx) == []
    advance(octx, 2, 1)
    assert records(octx) == [("missed", None)]


def test_two_consecutive_misses_pause_at_the_first_fire(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    paused: list[int] = []
    ck.auto_paused.connect(lambda: paused.append(1))
    start(octx)
    first = run_to_prompt(octx, step=1)
    advance(octx, 61, 1)
    assert records(octx) == [("missed", None)]
    assert str(octx.engine.state) == "focus"
    run_to_prompt(octx, step=1)
    advance(octx, 61, 1)
    assert records(octx) == [("missed", None), ("missed", None)]
    assert str(octx.engine.state) == "paused"
    assert paused == [1]
    assert octx.engine.focused_seconds == pytest.approx(first, abs=1.5)
    assert ck.paused_by_misses is True
    assert octx.pill.pill.time_text() == "Paused · were you away?"
    octx.engine.resume()
    assert ck.paused_by_misses is False
    assert octx.pill.pill.time_text() != "Paused · were you away?"
    # the focus after the first miss was rolled back: the next check-in is one interval away
    assert ck.seconds_until_next() == pytest.approx(BASE, abs=1)


def test_an_answer_between_misses_resets_the_rule(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx, minutes=120)
    run_to_prompt(octx)
    advance(octx, 65)
    run_to_prompt(octx)
    ck.answer("focused")
    run_to_prompt(octx)
    advance(octx, 65)
    assert str(octx.engine.state) == "focus"
    assert [r for r, _ in records(octx)] == ["missed", "focused", "missed"]


def test_pause_or_end_closes_the_prompt_without_record(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    run_to_prompt(octx)
    popup = ck.popup
    octx.engine.pause()
    assert ck.prompt_mode is None
    assert not PopupStack.instance().is_visible_panel(popup)
    octx.engine.resume()
    advance(octx, 120)
    assert records(octx) == []
    sid = octx.engine.session_id
    run_to_prompt(octx)
    octx.engine.finish()
    assert ck.prompt_mode is None
    assert records(octx, sid) == []


def test_inline_prompt_closed_on_pause(qapp: QApplication, data_dir: Any, fake_clock: Any) -> None:
    ctx = make_octx(qapp, data_dir, fake_clock, window=FakeWindowController(page="focus", visible=True, active=True))
    try:
        closed: list[int] = []
        ctx.checkins.prompt_closed.connect(lambda: closed.append(1))
        start(ctx)
        run_to_prompt(ctx)
        ctx.engine.pause()
        assert closed == [1]
        assert records(ctx) == []
    finally:
        dispose_octx(ctx, qapp)


# --------------------------------------------------------------------------- gating


def test_deferred_while_busy_then_shown_when_available(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    set_attention(monkeypatch, octx, "busy")
    advance(octx, BASE + 30)
    assert ck.prompt_mode is None and ck.is_deferred
    set_attention(monkeypatch, octx, "available")
    assert ck.prompt_mode == "popup"
    assert not ck.is_deferred


@pytest.mark.parametrize("state", ["quiet", "away"])
def test_deferred_while_quiet_or_away(octx: AppContext, monkeypatch: pytest.MonkeyPatch, state: str) -> None:  # noqa: F811
    start(octx)
    set_attention(monkeypatch, octx, state)
    advance(octx, BASE + 30)
    assert octx.checkins.prompt_mode is None and octx.checkins.is_deferred


def test_stale_deferred_checkin_is_dropped_and_only_one_shows(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx, minutes=120)
    set_attention(monkeypatch, octx, "busy")
    advance(octx, 2 * BASE + 100)  # past the second due time
    assert ck.prompt_mode is None
    assert ck.planner.next_due == pytest.approx(2 * BASE)  # the first one was dropped
    set_attention(monkeypatch, octx, "available")
    assert ck.prompt_mode == "popup"
    ck.answer("focused")
    assert records(octx) == [("focused", None)]
    # the next one is a full interval after the one that showed
    assert ck.seconds_until_next() == pytest.approx(BASE, abs=1)


def test_defer_retest_timer_fires_evaluate(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    set_attention(monkeypatch, octx, "busy")
    advance(octx, BASE + 5)
    assert ck._defer_timer.isActive()  # noqa: SLF001
    set_attention(monkeypatch, octx, "available", emit=False)
    ck._defer_timer.timeout.emit()  # noqa: SLF001 - the 15 s re-test
    assert ck.prompt_mode == "popup"


def test_idle_suppresses_without_record(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    set_idle(monkeypatch, octx, 400.0)
    advance(octx, BASE + 20)
    assert ck.prompt_mode is None and not ck.is_deferred
    assert records(octx) == []
    assert ck.seconds_until_next() == pytest.approx(BASE - 20, abs=11)
    set_idle(monkeypatch, octx, 1.0)
    run_to_prompt(octx)
    assert ck.prompt_mode == "popup"


def test_going_idle_during_an_open_prompt_records_nothing(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    start(octx)
    set_idle(monkeypatch, octx, 250.0)
    run_to_prompt(octx)
    set_idle(monkeypatch, octx, 320.0)
    advance(octx, 65)
    assert octx.checkins.prompt_mode is None
    assert records(octx) == []


def test_sound_played_on_fire(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    played: list[str] = []
    monkeypatch.setattr(octx.sound, "play", lambda ev: played.append(ev) or True)
    start(octx)
    run_to_prompt(octx)
    assert played == ["checkin"]


def test_restored_session_is_adopted(octx: AppContext) -> None:  # noqa: F811
    ck = octx.checkins
    start(octx)
    advance(octx, 200)
    ck._sid = None  # noqa: SLF001 - as if the service never saw session_started (restore)
    assert ck.seconds_until_next() == pytest.approx(BASE, abs=1)


def test_popup_without_service_is_a_preview(qapp: QApplication, octx: AppContext) -> None:  # noqa: F811
    popup = CheckInPopup(octx)
    popup.set_question("Plan", 60, 600, intro=True)
    popup.answer_focused()
    assert popup.phase() == "confirm"
    assert popup.confirmation() == "Nice — keep going"
    popup.hide()
    popup.deleteLater()


def test_panel_titles_are_balanced_without_a_one_word_widow(qapp) -> None:  # noqa: ANN001
    from PySide6.QtGui import QFontMetrics

    from madhura.ui import fonts
    from madhura.ui.overlays._panel_kit import ClampText, clamp_lines

    font = fonts.font(15, 600)
    text = "Still on Prepare slides for Thursday's sync?"
    width = QFontMetrics(font).horizontalAdvance("Still on Prepare slides for Thursday's") + 4
    assert clamp_lines(text, font, width, 2)[-1] == "sync?"  # plain greedy wrap
    balanced = clamp_lines(text, font, width, 2, balance=True)
    assert len(balanced) == 2 and " " in balanced[-1]
    assert " ".join(balanced) == text
    label = ClampText(text, 15, 600, 2)
    assert label.lines(width) == balanced
    assert clamp_lines(text * 3, font, width, 2, balance=True)[-1].endswith("…")  # still elides
