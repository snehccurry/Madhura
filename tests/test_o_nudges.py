"""[O] NudgeService + NudgePopup (spec §1.7, §5.3)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from test_o_support import (  # noqa: F401
    advance,
    dispose_octx,
    make_octx,
    octx,
    set_attention,
    set_idle,
)

from madhura.context import AppContext, FakeWindowController
from madhura.core.models import to_date_str
from madhura.services.nudge_service import (
    DONE_KEY,
    LAST_NUDGE_DATE_KEY,
    SNOOZE_KEY,
    WRAP_UP_KEY,
)
from madhura.ui.overlays.nudge_popup import NudgePopup, nudge_copy
from madhura.ui.widgets.popup import FloatingPanel, PopupStack

GRACE = 5 * 60


def wait(ctx: AppContext, seconds: float) -> None:
    """Idle time passes (no session): move the clock, then let the service evaluate."""
    ctx.clock.advance(seconds)  # type: ignore[attr-defined]
    ctx.engine.poll()
    ctx.nudges.check()


def set_now(ctx: AppContext, dt: datetime) -> None:
    ctx.clock.set(dt)  # type: ignore[attr-defined]
    ctx.engine.poll()


def add_session(ctx: AppContext, task: str, ended: datetime, minutes: int = 45, project: str | None = "Work") -> int:
    sid = ctx.repo.create_session(task, project, None, ended - timedelta(minutes=minutes), minutes * 60)
    ctx.repo.update_session(sid, focused_seconds=minutes * 60, outcome="completed", ended_at=ended)
    return sid


@pytest.fixture
def sounds(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # noqa: F811
    played: list[str] = []
    monkeypatch.setattr(octx.sound, "play", lambda ev: played.append(ev) or True)
    return played


# --------------------------------------------------------------------------- copy


def test_copy_variants() -> None:
    assert nudge_copy("next") == ("What's next?", "Plan your next block to stay intentional.")
    title, _ = nudge_copy("morning", task="Outline", hour_dt=datetime(2026, 10, 15, 9, 0))
    assert title == "Good morning — your first block: Outline"
    assert nudge_copy("paused", task="Write")[0] == "Still paused on ‘Write’"
    assert nudge_copy("wrap_up")[0] == "Wrap up your day?"


# --------------------------------------------------------------------------- gentle delivery


def test_gentle_nudge_after_idle_grace(octx: AppContext, sounds: list[str]) -> None:  # noqa: F811
    nudges = octx.nudges
    seen: list[str] = []
    nudges.nudged.connect(seen.append)
    wait(octx, GRACE - 1)
    assert nudges.panel is None
    wait(octx, 1)
    panel = nudges.panel
    assert isinstance(panel, NudgePopup) and panel.variant == "next"
    assert PopupStack.instance().is_visible_panel(panel)
    assert panel.priority == 4
    assert panel.title.text() == "What's next?"
    assert panel.primary_text() == "Plan now"
    assert seen == ["next"] and sounds == ["nudge"]
    assert octx.repo.kv_get(LAST_NUDGE_DATE_KEY) == "2026-10-14"


def test_repeats_every_idle_minutes_and_auto_hide_has_no_penalty(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    wait(octx, GRACE)
    panel = nudges.panel
    assert panel.auto_hide.duration_ms == 30_000
    panel.auto_hide.expire_now()
    QApplication.processEvents()
    assert panel.acted() == "timeout"
    assert nudges.panel is None
    assert not nudges.is_done_for_today and nudges.snoozed_until is None
    wait(octx, GRACE - 10)
    assert nudges.panel is None
    wait(octx, 10)
    assert nudges.panel is not None


def test_session_end_restarts_the_grace(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    octx.settings.update(checkin_enabled=False)  # unanswered check-ins would pause it
    octx.engine.start("Write", 25)
    advance(octx, 25 * 60, 30)  # completes → idle
    assert str(octx.engine.state) == "idle"
    nudges.check()
    assert nudges.panel is None
    wait(octx, GRACE)
    assert nudges.panel is not None


def test_panel_closes_when_a_session_starts(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    panel = octx.nudges.panel
    octx.engine.start("Write", 25)
    assert octx.nudges.panel is None
    assert not PopupStack.instance().is_visible_panel(panel)


def test_never_alongside_another_panel(octx: AppContext) -> None:  # noqa: F811
    other = FloatingPanel("checkin")
    body = QLabel("Still on it?")
    other.set_content(body)
    other.present()
    try:
        wait(octx, GRACE + 5)
        assert octx.nudges.panel is None
        other.dismiss(animate=False)
        octx.nudges.check()
        assert octx.nudges.panel is not None
    finally:
        other.hide()
        other.deleteLater()


def test_input_resuming_after_away_restarts_grace(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    nudges = octx.nudges
    wait(octx, GRACE - 60)
    set_attention(monkeypatch, octx, "away")
    wait(octx, 600)
    assert nudges.panel is None
    set_attention(monkeypatch, octx, "available")
    assert nudges.panel is None
    wait(octx, GRACE - 1)
    assert nudges.panel is None
    wait(octx, 1)
    assert nudges.panel is not None


# --------------------------------------------------------------------------- eligibility matrix


@pytest.mark.parametrize(
    "setup,expected",
    [
        ("baseline", "next"),
        ("mode_off", None),
        ("before_work", None),
        ("after_work", None),
        ("weekend", None),
        ("all_day", "next"),
        ("overnight_window", "next"),
        ("snoozed", None),
        ("done", None),
        ("busy", None),
        ("quiet", None),
        ("away", None),
        ("focus", None),
        ("break", None),
        ("paused_short", None),
        ("paused_long", "paused"),
    ],
)
def test_eligibility_matrix(octx: AppContext, monkeypatch: pytest.MonkeyPatch, setup: str, expected: str | None) -> None:  # noqa: F811
    nudges = octx.nudges
    s = octx.settings
    if setup == "mode_off":
        s.update(nudge_mode="off")
    elif setup == "before_work":
        set_now(octx, datetime(2026, 10, 14, 8, 30))
    elif setup == "after_work":
        set_now(octx, datetime(2026, 10, 14, 18, 0))
    elif setup == "weekend":
        set_now(octx, datetime(2026, 10, 17, 11, 0))
    elif setup == "all_day":
        s.update(work_start="00:00", work_end="00:00")
        set_now(octx, datetime(2026, 10, 14, 23, 30))
    elif setup == "overnight_window":
        s.update(work_start="22:00", work_end="06:00", work_days=1 << 2)  # Wednesday night shift
        set_now(octx, datetime(2026, 10, 15, 2, 0))  # Thursday 02:00 belongs to Wednesday's window
    elif setup == "snoozed":
        nudges.snooze(15)
    elif setup == "done":
        nudges.done_for_today()
    elif setup in ("busy", "quiet", "away"):
        set_attention(monkeypatch, octx, setup, emit=False)
    elif setup == "focus":
        octx.engine.start("Write", 25)
    elif setup == "break":
        octx.engine.start_break(5)
    elif setup == "paused_short":
        octx.engine.start("Write", 60)
        advance(octx, 60)
        octx.engine.pause()
        advance(octx, 19 * 60, 60)
    elif setup == "paused_long":
        octx.engine.start("Write", 60)
        advance(octx, 60)
        octx.engine.pause()
        advance(octx, 20 * 60, 60)
    assert nudges.eligible_variant() == expected


def test_snooze_expires(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    changed: list[int] = []
    nudges.state_changed.connect(lambda: changed.append(1))
    nudges.snooze(15)
    assert changed == [1]
    assert nudges.snoozed_until == datetime(2026, 10, 14, 10, 15)
    assert octx.repo.kv_get(SNOOZE_KEY) == "2026-10-14T10:15:00"
    wait(octx, 14 * 60)
    assert nudges.panel is None
    wait(octx, 61)
    assert nudges.snoozed_until is None
    assert nudges.panel is not None


def test_done_for_today_lasts_the_logical_day(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    nudges.done_for_today()
    assert nudges.is_done_for_today
    assert octx.repo.kv_get(DONE_KEY) == "2026-10-14"
    set_now(octx, datetime(2026, 10, 15, 3, 0))  # still Wednesday's logical day (starts at 04:00)
    assert nudges.is_done_for_today
    set_now(octx, datetime(2026, 10, 15, 9, 30))
    assert not nudges.is_done_for_today


def test_resume_clears_and_waits_one_grace(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    nudges.snooze(60)
    nudges.done_for_today()
    wait(octx, 30 * 60)
    nudges.resume()
    assert nudges.snoozed_until is None and not nudges.is_done_for_today
    nudges.check()
    assert nudges.panel is None
    wait(octx, GRACE)
    assert nudges.panel is not None


def test_paused_variant_after_twenty_minutes(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    octx.engine.start("Write the report", 60)
    advance(octx, 120)
    octx.engine.pause()
    advance(octx, 19 * 60, 60)
    nudges.check()
    assert nudges.panel is None
    advance(octx, 60, 60)
    nudges.check()
    panel = nudges.panel
    assert panel is not None and panel.variant == "paused"
    assert panel.title.text() == "Still paused on ‘Write the report’"
    assert panel.primary_text() == "Resume"
    assert panel.secondary_button.text() == "Finish"
    assert not panel.done_button.isVisibleTo(panel)
    label, _cb = nudges.primary_action()
    assert label == "Resume"
    panel.primary_button.click()
    assert str(octx.engine.state) == "focus"


def test_paused_variant_finish(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    advance(octx, 300)
    octx.engine.pause()
    advance(octx, 21 * 60, 60)
    octx.nudges.check()
    octx.nudges.panel.secondary_button.click()
    assert str(octx.engine.state) == "idle"


# --------------------------------------------------------------------------- firm


def test_firm_raises_without_focus_and_shows_banner(octx: AppContext, monkeypatch: pytest.MonkeyPatch, sounds: list[str]) -> None:  # noqa: F811
    nudges = octx.nudges
    win: FakeWindowController = octx.window  # type: ignore[assignment]
    octx.settings.update(nudge_mode="firm")
    changed: list[int] = []
    nudges.state_changed.connect(lambda: changed.append(1))
    set_idle(monkeypatch, octx, 0.5)  # typing right now
    wait(octx, GRACE)
    assert win.called("set_nudge_banner") == 0 and not nudges.banner_visible
    assert nudges._firm_retry.isActive()  # noqa: SLF001 - re-tested in 1 s
    set_idle(monkeypatch, octx, 3.0)
    nudges._firm_retry.timeout.emit()  # noqa: SLF001
    assert ("show_main", ("focus",), {"activate": False}) in win.calls
    assert win.nudge_banner is True
    assert nudges.banner_visible and changed == [1]
    assert nudges.panel is None
    assert sounds == ["nudge"]
    octx.engine.start("Write", 25)
    assert win.nudge_banner is False and not nudges.banner_visible


def test_firm_repeats_unless_the_user_is_on_the_focus_page(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    win: FakeWindowController = octx.window  # type: ignore[assignment]
    octx.settings.update(nudge_mode="firm")
    set_idle(monkeypatch, octx, None)
    wait(octx, GRACE)
    assert win.called("show_main") == 1
    wait(octx, GRACE - 1)
    assert win.called("show_main") == 1
    wait(octx, 1)  # still not active (raised without focus, buried again): raise again
    assert win.called("show_main") == 2
    win.active = True  # the user is looking at the banner
    wait(octx, GRACE)
    assert win.called("show_main") == 2
    win.page = "settings"
    wait(octx, GRACE)
    assert win.called("show_main") == 3


def test_firm_banner_hidden_by_snooze_and_mode_change(octx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    win: FakeWindowController = octx.window  # type: ignore[assignment]
    octx.settings.update(nudge_mode="firm")
    set_idle(monkeypatch, octx, 10.0)
    wait(octx, GRACE)
    assert octx.nudges.banner_visible
    octx.nudges.snooze(30)
    assert not octx.nudges.banner_visible and win.nudge_banner is False
    octx.nudges.resume()
    wait(octx, GRACE)
    assert octx.nudges.banner_visible
    octx.settings.update(nudge_mode="gentle")
    assert not octx.nudges.banner_visible


# --------------------------------------------------------------------------- primary action


def test_primary_plan_now(octx: AppContext) -> None:  # noqa: F811
    win: FakeWindowController = octx.window  # type: ignore[assignment]
    add_session(octx, "Old work", datetime(2026, 10, 14, 7, 30))  # > 2 h ago
    label, cb = octx.nudges.primary_action()
    assert label == "Plan now"
    cb()
    assert ("show_main", ("focus",), {"activate": True}) in win.calls
    assert win.task_focus_requests == 1


def test_primary_continue_recent_session(octx: AppContext) -> None:  # noqa: F811
    add_session(octx, "Refactor billing", datetime(2026, 10, 14, 9, 20), minutes=50, project="Core")
    label, cb = octx.nudges.primary_action()
    assert label == "Continue ‘Refactor billing’"
    cb()
    e = octx.engine
    assert str(e.state) == "focus" and e.task == "Refactor billing" and e.project == "Core"
    assert e.planned_seconds == 50 * 60


def test_primary_starts_first_up_next_task(octx: AppContext) -> None:  # noqa: F811
    add_session(octx, "Refactor billing", datetime(2026, 10, 14, 9, 20))
    tid = octx.repo.add_task("Draft the onboarding email", 25, "Growth", plan_date=octx.today())
    octx.repo.add_task("Second thing", 45, None, plan_date=octx.today())
    label, cb = octx.nudges.primary_action()
    assert label == "Start ‘Draft the onboarding email’ · 25m"
    cb()
    e = octx.engine
    assert e.task == "Draft the onboarding email" and e.planned_task_id == tid
    assert e.project == "Growth" and e.planned_seconds == 25 * 60


def test_primary_skips_done_tasks_and_handles_vanished_task(octx: AppContext) -> None:  # noqa: F811
    t1 = octx.repo.add_task("Done already", 25, None, plan_date=octx.today())
    octx.repo.set_task_done(t1, True)
    t2 = octx.repo.add_task("Next up", 60, None, plan_date=octx.today())
    label, cb = octx.nudges.primary_action()
    assert label == "Start ‘Next up’ · 1h"
    octx.repo.delete_task(t2)
    cb()
    assert str(octx.engine.state) == "idle"
    assert octx.window.task_focus_requests == 1  # type: ignore[union-attr]


def test_long_task_titles_are_elided_in_the_label(octx: AppContext) -> None:  # noqa: F811
    octx.repo.add_task("Write the quarterly report for the leadership offsite in Lisbon", 45, None, plan_date=octx.today())
    label, _cb = octx.nudges.primary_action()
    assert label.startswith("Start ‘Write the quarterly report") and "…’ · 45m" in label


def test_popup_primary_click_runs_the_action(octx: AppContext) -> None:  # noqa: F811
    octx.repo.add_task("Outline", 25, None, plan_date=octx.today())
    add_session(octx, "Earlier", datetime(2026, 10, 14, 9, 0))  # a session today → not "morning"
    wait(octx, GRACE)
    panel = octx.nudges.panel
    assert panel.variant == "next"
    assert panel.primary_text() == "Start ‘Outline’ · 25m"
    panel.primary_button.click()
    assert panel.acted() == "primary"
    assert octx.engine.task == "Outline"


# --------------------------------------------------------------------------- popup menu / buttons


def test_snooze_menu_items(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    panel = octx.nudges.panel
    menu = panel.snooze_menu()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["15 min", "30 min", "1 hour", "In a meeting…"]
    sub = [a for a in menu.actions() if a.menu() is not None][0].menu()
    assert [a.text() for a in sub.actions()] == ["30 min", "1 hour"]
    menu.actions()[1].trigger()  # 30 min
    assert octx.nudges.snoozed_until == datetime(2026, 10, 14, 10, 35)
    assert panel.acted() == "snooze"
    assert octx.nudges.panel is None


@pytest.mark.parametrize("minutes", [30, 60])
def test_in_a_meeting_starts_a_meeting_block(octx: AppContext, minutes: int) -> None:  # noqa: F811
    wait(octx, GRACE)
    panel = octx.nudges.panel
    panel.meeting(minutes)
    e = octx.engine
    assert str(e.state) == "break" and e.break_label == "Meeting"
    assert e.break_total_seconds == minutes * 60


def test_done_for_today_button(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    octx.nudges.panel.done_button.click()
    assert octx.nudges.is_done_for_today
    wait(octx, 2 * GRACE)
    assert octx.nudges.panel is None


def test_close_button_dismisses_without_penalty(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    panel = octx.nudges.panel
    panel.close_button.click()
    assert panel.acted() == "dismiss"
    assert octx.nudges.snoozed_until is None and not octx.nudges.is_done_for_today


def test_hover_pauses_auto_hide(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    panel = octx.nudges.panel
    panel.hover_changed.emit(True)
    assert panel.auto_hide.is_paused()
    panel.hover_changed.emit(False)
    assert not panel.auto_hide.is_paused() and panel.auto_hide.remaining_ms() >= 8_000


# --------------------------------------------------------------------------- morning + wrap-up


def test_first_nudge_of_the_day_greets_with_the_first_block(octx: AppContext) -> None:  # noqa: F811
    set_now(octx, datetime(2026, 10, 15, 9, 0))
    octx.repo.add_task("Outline the Q4 roadmap", 60, None, plan_date=octx.today())
    octx.nudges.check()
    wait(octx, GRACE)
    panel = octx.nudges.panel
    assert panel.variant == "morning"
    assert panel.title.text() == "Good morning — your first block: Outline the Q4 roadmap"
    assert panel.primary_text() == "Start now · 1h"
    panel.auto_hide.expire_now()
    QApplication.processEvents()
    wait(octx, GRACE)
    assert octx.nudges.panel.variant == "next"


def test_no_morning_copy_without_a_planned_task(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    assert octx.nudges.panel.variant == "next"


def test_wrap_up_prompt_once_per_day_after_work_end(octx: AppContext) -> None:  # noqa: F811
    win: FakeWindowController = octx.window  # type: ignore[assignment]
    seen: list[str] = []
    octx.nudges.nudged.connect(seen.append)
    set_now(octx, datetime(2026, 10, 14, 17, 59))
    octx.nudges.check()
    assert octx.nudges.panel.variant == "next"  # still work hours
    octx.nudges.panel.dismiss(animate=False)
    set_now(octx, datetime(2026, 10, 14, 18, 2))
    octx.nudges.check()  # idle since app start (10:00): the grace has long passed
    panel = octx.nudges.panel
    assert panel is not None and panel.variant == "wrap_up"
    assert seen == ["next", "wrap_up"]
    assert panel.title.text() == "Wrap up your day?"
    assert octx.repo.kv_get(WRAP_UP_KEY) == "2026-10-14"
    panel.primary_button.click()
    assert [c[1] for c in win.calls if c[0] == "show_main"][-1] == ("focus",)
    from madhura.ui.overlays.wrap_up import WrapUpSheet

    assert any(isinstance(s, WrapUpSheet) for s in win.sheets)
    wait(octx, 3 * GRACE)
    assert octx.nudges.panel is None  # once per logical day


def test_wrap_up_respects_grace_work_days_and_done(octx: AppContext) -> None:  # noqa: F811
    nudges = octx.nudges
    octx.engine.start("Late work", 30)
    set_now(octx, datetime(2026, 10, 14, 18, 30))
    octx.engine.finish()  # idle from 18:30
    nudges.check()
    assert not nudges.wrap_up_due()
    octx.clock.advance(GRACE)  # type: ignore[attr-defined]
    assert nudges.wrap_up_due()
    nudges.done_for_today()
    assert not nudges.wrap_up_due()
    nudges.resume()
    set_now(octx, datetime(2026, 10, 17, 19, 0))  # Saturday: no work window ended today
    assert not nudges.wrap_up_due()
    octx.repo.kv_set(WRAP_UP_KEY, to_date_str(octx.today()))
    set_now(octx, datetime(2026, 10, 16, 19, 0))  # Friday evening
    assert nudges.wrap_up_due()


def test_mode_off_shows_nothing(octx: AppContext) -> None:  # noqa: F811
    octx.settings.update(nudge_mode="off")
    wait(octx, 3 * GRACE)
    assert octx.nudges.panel is None
    set_now(octx, datetime(2026, 10, 14, 19, 0))
    octx.nudges.check()
    assert octx.nudges.panel is None


def test_mode_off_closes_an_open_panel(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    assert octx.nudges.panel is not None
    octx.settings.update(nudge_mode="off")
    assert octx.nudges.panel is None


def test_works_with_the_real_main_window(
    qapp: QApplication, data_dir: Any, fake_clock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_shell_support import dispose_shell, make_shell

    ctx = make_shell(qapp, data_dir, fake_clock)
    try:
        ctx.settings.update(nudge_mode="firm")
        monkeypatch.setattr(ctx.attention, "input_idle_seconds", lambda: None)
        wait(ctx, GRACE)
        page = ctx.window.pages["focus"]  # type: ignore[union-attr]
        assert ctx.nudges.banner_visible
        assert page.nudge_banner_visible()
        ctx.nudges.snooze(15)
        assert not page.nudge_banner_visible()
    finally:
        dispose_shell(ctx, qapp)


def test_snooze_button_opens_its_menu_and_pauses_auto_hide(octx: AppContext) -> None:  # noqa: F811
    wait(octx, GRACE)
    panel = octx.nudges.panel
    panel.snooze_button.click()
    menu = panel._menu  # noqa: SLF001
    assert menu is not None and menu.isVisible()
    assert panel.auto_hide.is_paused()
    menu.close()
    QApplication.processEvents()
    assert not panel.auto_hide.is_paused()


def test_popup_primary_label_is_elided_to_fit(octx: AppContext) -> None:  # noqa: F811
    from PySide6.QtGui import QFontMetrics

    title = "Write the quarterly report for the leadership offsite in Lisbon, with appendix"
    octx.repo.add_task(title, 45, None, plan_date=octx.today())
    add_session(octx, "Earlier", datetime(2026, 10, 14, 9, 0))
    wait(octx, GRACE)
    panel = octx.nudges.panel
    text = panel.primary_text()
    assert text.startswith("Start ‘Write the quarterly") and text.endswith("…’ · 45m")
    fm = QFontMetrics(panel.primary_button.font())
    assert fm.horizontalAdvance(text) + 56 <= panel.card_width() - 2 * panel.padding
    assert panel.primary_button.toolTip() == f"Start ‘{title}’ · 45m"
