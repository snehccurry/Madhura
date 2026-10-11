"""InsightsPage [I]: empty state, KPIs + deltas, charts, ranges, recent sessions (delete + undo),
export, refresh rules, layout."""

# ruff: noqa: F811 - pytest fixtures are imported from test_i_support and used as parameters
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from madhura.context import AppContext
from madhura.core import analytics as an
from madhura.ui import animations, dialogs, theme
from madhura.ui.pages import _insights_kit as kit
from madhura.ui.pages.insights_page import FADE_MS, KPI_FOUR_COLUMNS_MIN, SIDE_BY_SIDE_MIN, InsightsPage, _FadeOverlay, _KpiGrid
from madhura.ui.widgets.stat_card import StatCard
from test_i_support import TODAY, add_session, ictx, key, load_seed_demo, no_summary, pump, show, win_of  # noqa: F401


def page_of(ctx: AppContext) -> InsightsPage:
    page = win_of(ctx).pages["insights"]
    assert isinstance(page, InsightsPage)
    return page


def seed_week(ctx: AppContext) -> dict[str, int]:
    """Known data around Wed 14 Oct 10:00 (day starts at 04:00)."""
    ids = {
        "today": add_session(ctx, datetime(2026, 10, 14, 8, 0), 60, project="Madhura", task="Polish charts",
                             checkins=("focused", "focused", "focused", ("drifted", "Phone"))),
        "late": add_session(ctx, datetime(2026, 10, 14, 2, 30), 30, project="Madhura", task="Polish charts"),
        "tue": add_session(ctx, datetime(2026, 10, 13, 15, 0), 45, "ended_early", project="Thesis", task="Draft chapter 3",
                           checkins=("focused", ("drifted", "Phone"), ("drifted", "Email")), note="Kept getting pulled away"),
        "mon": add_session(ctx, datetime(2026, 10, 12, 9, 0), 90, task="Inbox zero", checkins=("focused", "focused")),
        "abandoned": add_session(ctx, datetime(2026, 10, 11, 10, 0), 20, "abandoned", project="Learning", task="Rust"),
        "prev": add_session(ctx, datetime(2026, 10, 5, 10, 0), 120, project="Madhura", checkins=("focused",) * 4),
    }
    ctx.repo.create_session("Running now", None, None, datetime(2026, 10, 14, 9, 30), 1500)  # active: never shown
    return ids


# =========================================================================== empty state


def test_empty_state_when_no_sessions_ever(ictx: AppContext) -> None:
    show(ictx)
    page = page_of(ictx)
    assert page.is_empty_state()
    assert page.empty_card.isVisible() and not page.body.isVisible()
    assert not page.range_control.isVisible() and not page.export_button.isVisible()
    assert page.header.title_label.text() == "Insights"
    page.plan_button.click()
    pump()
    assert win_of(ictx).current_page() == "focus"


def test_only_active_rows_still_empty(ictx: AppContext) -> None:
    ictx.repo.create_session("Running", None, None, datetime(2026, 10, 14, 9, 0), 1500)
    show(ictx)
    assert page_of(ictx).is_empty_state()


def test_empty_state_turns_into_page_when_a_session_finishes(ictx: AppContext) -> None:
    show(ictx)
    page = page_of(ictx)
    assert page.is_empty_state()
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 25)
    ictx.events.data_changed.emit("sessions")
    pump()
    assert not page.is_empty_state() and page.body.isVisible()


# =========================================================================== KPIs


def test_kpis_values_deltas_and_hints(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    assert page.current_range() == "7d"
    snap = page.snapshot()
    assert snap is not None and snap.focus_seconds == 225 * 60 and snap.session_count == 4

    assert page.kpi_focus.value_text() == "3h 45m"
    assert page.kpi_focus.delta_text() == "▲ 88%" and page.kpi_focus.delta_tone() == "success"
    assert page.kpi_focus.caption_text() == "vs previous 7 days"
    assert page.kpi_focus.hint_text() == "Best streak 1"

    assert page.kpi_sessions.value_text() == "4"
    assert page.kpi_sessions.delta_text() == "▲ 300%"

    assert page.kpi_score.value_text() == "67%"
    assert page.kpi_score.delta_text() == "▼ 33 pts" and page.kpi_score.delta_tone() == "muted"
    assert page.kpi_score.hint_text() == "Came back 3 times"

    assert page.kpi_habits.value_text() == "—" and page.kpi_habits.delta_text() == "—"


def test_streak_hint_and_habits_kpi(ictx: AppContext) -> None:
    ictx.settings.update(daily_goal_minutes=60)
    for back in range(3):  # Mon 12, Tue 13, Wed 14 each 60 min
        add_session(ictx, datetime(2026, 10, 14 - back, 9, 0), 60)
    hid = ictx.repo.add_habit("Read")
    ictx.repo.update_habit(hid, created_at=datetime(2026, 10, 1, 9, 0))
    for d in (8, 9, 10, 11, 12):
        ictx.repo.set_habit_status(hid, date(2026, 10, d), "done")
    ictx.repo.set_habit_status(hid, date(2026, 10, 13), "skipped")
    show(ictx)
    page = page_of(ictx)
    assert page.kpi_focus.hint_text() == "Streak 3 · best 3"
    # Oct 8–12 done (5), 13 skipped (neutral), 14 = today pending (not counted) → 100 %
    assert page.kpi_habits.value_text() == "100%" and page.kpi_habits.hint_text() == "5 of 5 done"
    # previous 7 days: Oct 1–7 scheduled, nothing done → 0 % → +100 pts
    assert page.kpi_habits.delta_text() == "▲ 100 pts"


def test_habits_kpi_counts_a_habit_created_after_midnight(ictx: AppContext) -> None:
    ictx.clock.set(datetime(2026, 10, 15, 1, 40))  # type: ignore[attr-defined]  # logical Wed 14 Oct
    hid = ictx.repo.add_habit("Stretch")
    ictx.repo.update_habit(hid, created_at=datetime(2026, 10, 15, 1, 30))
    ictx.repo.set_habit_status(hid, date(2026, 10, 14), "done")
    snap = kit.load_snapshot(ictx, "7d")
    assert snap.today == date(2026, 10, 14)
    # the Habits page counts it done today; Insights agrees
    assert an.habit_day_counts(ictx.repo.habits(), ictx.repo.habit_logs_for_date(snap.today), snap.today) == (1, 1)
    assert (snap.habit_done, snap.habit_scheduled) == (1, 1)


def test_counting_rule_excludes_abandoned_and_active(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 40, "abandoned", checkins=("drifted",))
    add_session(ictx, datetime(2026, 10, 13, 8, 0), 25)
    ictx.repo.create_session("Running", None, None, datetime(2026, 10, 14, 9, 30), 1500)
    show(ictx)
    page = page_of(ictx)
    assert page.kpi_focus.value_text() == "25m" and page.kpi_sessions.value_text() == "1"
    assert page.kpi_score.value_text() == "—"  # the abandoned session's check-in does not count
    assert len(page.recent_list.rows) == 2


# =========================================================================== charts


def test_bar_chart_7d(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    bars = page.chart.bars()
    assert len(bars) == 7
    assert [b.label for b in bars] == ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"]
    assert [b.value for b in bars][-3:] == [5400, 75 * 60, 3600]
    assert bars[-1].current and not any(b.current for b in bars[:-1])
    assert bars[-2].title == "Tue 13 Oct" and bars[-2].detail == "1h 15m · 2 sessions"
    assert bars[0].detail == "No focus"
    assert page.chart.goal() == 7200 and page.chart.goal_label() == "Goal 2h"
    assert page.chart_card.title_label().text() == "Daily focus"
    assert page.chart_average.text() == "Avg 32m a day"
    desc = page.chart.accessibleDescription()
    assert "3h 45m in total" in desc and "Best day: Mon 12 Oct with 1h 30m" in desc and "met in 0 of 7 days" in desc


def test_ranges_30d_and_12w(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    page.set_range("30d")
    bars = page.chart.bars()
    assert len(bars) == 30
    labelled = [i for i, b in enumerate(bars) if b.label]
    assert labelled == [4, 9, 14, 19, 24, 29]  # every 5th date, counted back from today
    assert bars[29].label == "14 Oct" and bars[24].label == "9 Oct"
    assert page.kpi_focus.caption_text() == "vs previous 30 days"
    assert page.kpi_focus.value_text() == "5h 45m"  # includes the 5 Oct session

    page.set_range("12w")
    bars = page.chart.bars()
    assert len(bars) == 12
    assert bars[-1].label == "12 Oct" and bars[-1].current and bars[-1].title == "Week of 12 Oct"
    assert bars[-1].value == 225 * 60 and bars[-2].value == 120 * 60
    assert page.chart.goal() == 120 * 60 * 5 and page.chart.goal_label() == "Goal 10h"
    assert page.chart_card.title_label().text() == "Weekly focus"
    assert page.chart_average.text().endswith("a week")
    assert page.kpi_focus.caption_text() == "vs previous 12 weeks"


def test_range_switch_via_control_persists(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    page.range_control.valueChanged.emit("30d")
    pump()
    assert page.current_range() == "30d" and page.snapshot().range_key == "30d"  # type: ignore[union-attr]
    assert ictx.repo.kv_get(kit.RANGE_KEY) == "30d"
    # A new page (next launch) starts on the stored range.
    again = InsightsPage(ictx)
    assert again.current_range() == "30d" and again.range_control.value() == "30d"
    again.deleteLater()


def test_range_switch_cross_fades_and_counts_up(ictx: AppContext, qtbot) -> None:  # noqa: ANN001
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    ictx.settings.update(reduce_motion=False)
    assert animations.motion_enabled()
    page.set_range("30d")
    overlays = page.body.findChildren(_FadeOverlay)
    assert len(overlays) == 1 and overlays[0].isVisible()
    assert page.kpi_focus.displayed_text() != page.kpi_focus.value_text()  # counting up
    qtbot.wait(FADE_MS + 300)
    pump()
    assert page.kpi_focus.displayed_text() == page.kpi_focus.value_text() == "5h 45m"
    qtbot.waitUntil(lambda: not page.body.findChildren(_FadeOverlay), timeout=2000)


def test_reduce_motion_switches_instantly(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    page.set_range("12w")
    assert not page.body.findChildren(_FadeOverlay)
    assert page.kpi_focus.displayed_text() == page.kpi_focus.value_text()


def test_heatmap_and_patterns_note(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 30)
    add_session(ictx, datetime(2026, 10, 13, 8, 0), 30)
    show(ictx)
    page = page_of(ictx)
    assert not page.heatmap.has_enough_data()
    add_session(ictx, datetime(2026, 10, 12, 9, 40), 45)
    ictx.events.data_changed.emit("sessions")
    pump()
    assert page.heatmap.has_enough_data()
    assert page.heatmap.hours() == (6, 23)
    assert page.heatmap.value(0, 9) == pytest.approx(20 * 60) and page.heatmap.value(0, 10) == pytest.approx(25 * 60)


def test_heatmap_widens_for_early_hours(ictx: AppContext) -> None:
    seed_week(ictx)  # the 02:30 session falls in 00–05
    show(ictx)
    assert page_of(ictx).heatmap.hours() == (0, 23)


def test_top_projects_and_tasks(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    assert page.top_mode() == "projects"
    items = page.top_list.items()
    assert [i.label for i in items] == ["Madhura", "No project", "Thesis"]
    assert items[0].value_text == "1h 30m"
    assert items[0].color == theme.project_color("Madhura")
    assert items[1].color == theme.palette().text_subtle
    assert "2 sessions" in items[0].tooltip and "40% of your focus" in items[0].tooltip
    page.top_toggle.valueChanged.emit("tasks")
    assert page.top_mode() == "tasks"
    assert page.top_card.title_label().text() == "Top tasks"
    assert [i.label for i in page.top_list.items()][:2] == ["Polish charts", "Inbox zero"]


def test_top_defaults_to_tasks_without_projects(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 30, task="Read")
    show(ictx)
    page = page_of(ictx)
    assert page.top_mode() == "tasks" and [i.label for i in page.top_list.items()] == ["Read"]


def test_drift_panel_states(ictx: AppContext) -> None:
    sid = add_session(ictx, datetime(2026, 10, 14, 8, 0), 30)
    show(ictx)
    page = page_of(ictx)
    assert page.drift.headline.text() == "No check-ins in this range" and page.drift.reasons.isHidden()
    ictx.repo.add_checkin(sid, datetime(2026, 10, 14, 8, 7), "focused")
    ictx.events.data_changed.emit("checkins")
    pump()
    assert page.drift.headline.text() == "Nothing pulled you away"
    ictx.repo.add_checkin(sid, datetime(2026, 10, 14, 8, 14), "drifted", "Phone")
    ictx.repo.add_checkin(sid, datetime(2026, 10, 14, 8, 21), "drifted")
    ictx.events.data_changed.emit("checkins")
    pump()
    assert page.drift.headline.text() == "You noticed and came back 2 times"
    assert [(i.label, i.value_text) for i in page.drift.reasons.items()] == [("Phone", "1×")]
    assert not page.drift.reasons.isHidden()


def test_drift_panel_stacks_on_narrow_cards(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    assert not page.drift.is_stacked()
    page.drift.resize(500, page.drift.height())
    assert page.drift.is_stacked()
    page.drift.resize(700, page.drift.height())
    assert not page.drift.is_stacked()


# =========================================================================== recent sessions


def test_recent_rows(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    rows = page.recent_list.rows
    assert [r.session.id for r in rows] == [ids["today"], ids["late"], ids["tue"], ids["mon"], ids["abandoned"]]
    assert [r.when_label.text() for r in rows] == [
        "Today, 08:00",
        "Yesterday, 02:30",
        "Yesterday, 15:00",
        "Mon 12 Oct, 09:00",
        "Sun 11 Oct, 10:00",
    ]
    today_row = rows[0]
    assert today_row.title.full_text() == "Polish charts"
    assert today_row.project_badge is not None and today_row.project_badge.text() == "Madhura"
    assert today_row.duration_label.text() == "1h" and today_row.score_label.text() == "75%"
    assert today_row.score_label.toolTip() == "3 of 4 check-ins focused"
    assert today_row.outcome_badge is None
    tue = rows[2]
    assert tue.outcome_badge is not None and tue.outcome_badge.text() == "Ended early"
    assert tue.note_icon.toolTip() == "Kept getting pulled away"
    assert rows[3].project_badge is None and rows[3].score_label.text() == "100%"
    ab = rows[4]
    assert ab.outcome_badge is not None and ab.outcome_badge.text() == "Abandoned"
    assert ab.score_label.text() == ""
    assert page.recent_count.text() == "5 sessions"


def test_recent_limited_to_50(ictx: AppContext) -> None:
    start = datetime(2026, 10, 8, 6, 0)
    for i in range(55):
        add_session(ictx, start + timedelta(minutes=30 * i), 10)
    show(ictx)
    page = page_of(ictx)
    assert len(page.recent_list.rows) == 50
    assert page.recent_count.text() == "Last 50 in this range"


def test_delete_session_with_undo(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    win = win_of(ictx)
    page.delete_session(ids["today"])
    pump()
    assert ids["today"] not in [r.session.id for r in page.recent_list.rows]
    assert ictx.repo.get_session(ids["today"]) is not None  # not deleted yet
    assert page.kpi_focus.value_text() == "2h 45m"  # excluded from the numbers right away
    assert win.toasts.is_showing() and win.toasts.text() == "Deleted ‘Polish charts’" and win.toasts.action_text() == "Undo"
    win.toasts.trigger_action()
    pump()
    assert ids["today"] in [r.session.id for r in page.recent_list.rows]
    assert page.kpi_focus.value_text() == "3h 45m"
    assert ictx.repo.get_session(ids["today"]) is not None


def test_delete_commits_when_toast_expires(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    seen: list[str] = []
    ictx.events.data_changed.connect(seen.append)
    page.delete_session(ids["tue"])
    assert ictx.repo.get_session(ids["tue"]) is not None
    win_of(ictx).flush_toasts()  # timeout / quit
    pump()
    assert ictx.repo.get_session(ids["tue"]) is None
    assert ictx.repo.checkins_for_session(ids["tue"]) == []
    assert "sessions" in seen
    assert page.undo.ids() == set()
    assert ids["tue"] not in [r.session.id for r in page.recent_list.rows]


def test_second_delete_commits_the_first(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    page.delete_session(ids["today"])
    page.delete_session(ids["mon"])  # replaces the toast → first one expires
    pump()
    assert ictx.repo.get_session(ids["today"]) is None
    assert ictx.repo.get_session(ids["mon"]) is not None
    assert page.undo.ids() == {ids["mon"]}


def test_recent_keyboard_delete_and_menu(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    lst = page.recent_list
    lst.setFocus(Qt.FocusReason.TabFocusReason)
    pump()
    assert lst.selected_index() == 0 and lst.rows[0].is_selected()
    assert lst.accessibleDescription() == f"{lst.rows[0].accessibleName()}, 1 of {len(lst.rows)}"
    key(lst, Qt.Key.Key_Down)
    key(lst, Qt.Key.Key_Down)
    assert lst.selected_index() == 2
    assert lst.accessibleDescription() == f"{lst.rows[2].accessibleName()}, 3 of {len(lst.rows)}"  # announced
    key(lst, Qt.Key.Key_End)
    assert lst.selected_index() == 4
    key(lst, Qt.Key.Key_Home)
    key(lst, Qt.Key.Key_Delete)
    pump()
    assert ids["today"] in page.undo.ids()
    menu = lst.rows[0].open_menu()
    assert [a.text() for a in menu.actions()] == ["Delete session"]
    menu.close()


def test_recent_delete_keeps_the_selection_in_place(ictx: AppContext) -> None:
    """After Delete the selection is the row that took the deleted one's place — not the newest
    session (which a second Delete would then remove) — and a held key deletes only one."""
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    lst = page.recent_list
    lst.setFocus(Qt.FocusReason.TabFocusReason)
    pump()
    n = len(lst.rows)
    lst.select(2)
    deleted, below = lst.rows[2].session.id, lst.rows[3].session.id
    key(lst, Qt.Key.Key_Delete)
    pump()
    assert page.undo.ids() == {deleted} and len(lst.rows) == n - 1
    assert lst.hasFocus() and lst.selected_index() == 2 and lst.rows[2].session.id == below
    assert lst.rows[2].is_selected() and not lst.rows[0].is_selected()
    # Auto-repeat of a held Delete / Backspace is ignored.
    for k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        QApplication.sendEvent(lst, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, "", True))
    pump()
    assert page.undo.ids() == {deleted} and lst.rows[2].session.id == below
    # Deleting the last row selects the new last row.
    key(lst, Qt.Key.Key_End)
    key(lst, Qt.Key.Key_Delete)
    pump()
    assert lst.selected_index() == len(lst.rows) - 1 == n - 3


def test_ctrl_z_undoes_a_delete_from_the_keyboard(ictx: AppContext) -> None:
    seed_week(ictx)
    win = show(ictx)
    win.activateWindow()
    pump()
    assert QApplication.activeWindow() is win
    page = page_of(ictx)
    lst = page.recent_list
    lst.setFocus(Qt.FocusReason.TabFocusReason)
    pump()
    sid = lst.rows[0].session.id
    key(lst, Qt.Key.Key_Delete)
    pump()
    assert sid in page.undo.ids() and win.toasts.action_text() == "Undo"
    QTest.keyClick(lst, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    pump()
    assert page.undo.ids() == set() and not win.toasts.is_showing()
    assert sid in [r.session.id for r in lst.rows] and ictx.repo.get_session(sid) is not None
    assert lst.hasFocus()


def test_row_menu_deletes(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    row = page.recent_list.rows[1]
    assert row.more_button.accessibleName() == "More actions for ‘Polish charts’"
    menu = row.open_menu()
    menu.actions()[0].trigger()
    pump()
    assert ids["late"] in page.undo.ids()


# =========================================================================== export


def test_export_csv_of_selected_range(ictx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    target = tmp_path / "out"
    asked: list[tuple[str, str]] = []

    def fake_save(_parent, title, default_name="", filter=""):  # noqa: ANN001, A002
        asked.append((title, default_name))
        return str(target)

    monkeypatch.setattr(dialogs, "get_save_path", fake_save)
    menu = page.open_export_menu()
    assert [a.text() for a in menu.actions()] == ["Export sessions (CSV)…"]
    menu.close()
    assert page.export_csv() is True
    assert asked and asked[0][0] == "Export sessions"
    assert asked[0][1].endswith("madhura-sessions-2026-10-08-to-2026-10-14.csv")
    out = tmp_path / "out.csv"
    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert sorted(int(r["id"]) for r in rows) == sorted(ids[k] for k in ("today", "late", "tue", "mon", "abandoned"))
    assert win_of(ictx).toasts.text() == "Exported 5 sessions to out.csv"


def test_export_cancel_and_error(ictx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    monkeypatch.setattr(dialogs, "get_save_path", lambda *a, **k: None)
    assert page.export_csv() is False
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(dialogs, "get_save_path", lambda *a, **k: str(tmp_path / "x.csv"))
    monkeypatch.setattr(dialogs, "show_error", lambda _p, title, text: errors.append((title, text)))
    from madhura.data import export as export_mod

    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("Permission denied")

    monkeypatch.setattr(export_mod, "export_sessions_csv", boom)
    assert page.export_csv() is False
    assert errors == [("Couldn't export sessions", "Permission denied")]


# =========================================================================== refresh rules


def test_refreshes_when_shown_after_changes(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 25)
    show(ictx)
    page = page_of(ictx)
    assert page.kpi_sessions.value_text() == "1"
    win_of(ictx).show_page("focus")
    pump()
    add_session(ictx, datetime(2026, 10, 13, 8, 0), 25)
    ictx.events.data_changed.emit("sessions")
    pump()
    assert page.kpi_sessions.value_text() == "1"  # hidden: only marked dirty
    win_of(ictx).show_page("insights")
    pump()
    assert page.kpi_sessions.value_text() == "2"


def test_ignores_unrelated_changes(ictx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 25)
    show(ictx)
    page = page_of(ictx)
    calls: list[bool] = []
    monkeypatch.setattr(page, "refresh", lambda animate=False: calls.append(animate))
    ictx.events.data_changed.emit("captures")
    ictx.events.data_changed.emit("tasks")
    pump()
    assert calls == []
    ictx.events.data_changed.emit("habits")
    pump()
    assert calls == [True]


def test_day_rollover_moves_the_window(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 8, 9, 0), 25)  # first day of the 7-day range
    show(ictx)
    page = page_of(ictx)
    assert page.kpi_sessions.value_text() == "1"
    ictx.clock.set(datetime(2026, 10, 15, 4, 30))  # type: ignore[attr-defined]
    ictx.events.day_changed.emit()
    pump()
    assert page.snapshot().today == date(2026, 10, 15)  # type: ignore[union-attr]
    assert page.kpi_sessions.value_text() == "0"


def test_day_start_hour_setting_changes_buckets(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 14, 2, 0), 30)
    show(ictx)
    page = page_of(ictx)
    assert page.chart.bars()[-2].value == 1800  # 02:00 belongs to Tuesday
    ictx.settings.update(day_start_hour=0)
    pump()
    assert page.chart.bars()[-1].value == 1800 and page.chart.bars()[-2].value == 0


def test_theme_switch_rerenders(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    ictx.settings.update(theme="light")
    pump()
    row = page.recent_list.rows[0]
    assert row.project_badge is not None
    assert row.project_badge._dot.name().upper() == theme.project_color("Madhura").upper()  # noqa: SLF001
    assert page.top_list.items()[0].color == theme.project_color("Madhura")
    page.widget().grab()  # paints in light


# =========================================================================== layout


def test_side_by_side_and_stacked(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx, width=1120)
    page = page_of(ictx)
    assert page.is_side_by_side()
    assert page.heatmap_card.geometry().top() == page.top_card.geometry().top()
    # Titles share a baseline although only "Top projects" has a 28 px toggle in its header.
    heat_title, top_title = page.heatmap_card.title_label(), page.top_card.title_label()
    assert heat_title is not None and top_title is not None
    centre = lambda lbl: lbl.mapTo(page.row, lbl.rect().center()).y()  # noqa: E731
    assert centre(heat_title) == centre(top_title)
    win_of(ictx).resize(SIDE_BY_SIDE_MIN - 60, 700)
    pump(10)
    assert not page.is_side_by_side()
    assert page.top_card.geometry().top() > page.heatmap_card.geometry().bottom()


def test_kpi_grid_columns(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx, width=1120)
    page = page_of(ictx)
    assert page.kpi_grid.columns() == 2  # 840 px content → 2×2 so hints never truncate
    page.kpi_grid.resize(1100, page.kpi_grid.height())
    assert page.kpi_grid.columns() == 4 and page.kpi_grid.height() == 108
    page.kpi_grid.resize(700, 108)
    assert page.kpi_grid.columns() == 2 and page.kpi_grid.height() == 108 * 2 + 16


@pytest.mark.parametrize("width", [KPI_FOUR_COLUMNS_MIN, 1120])
def test_kpi_hints_never_truncate_in_four_columns(ictx: AppContext, width: int) -> None:
    """From the 4-up threshold on, three-digit hints still fit next to their labels."""
    hints = {"Focus time": "Streak 123 · best 456", "Sessions": "", "Focus score": "Came back 122 times",
             "Habits": "612 of 840 done"}
    cards = [StatCard(label) for label in hints]
    for card, hint in zip(cards, hints.values()):
        card.set_hint(hint)
    grid = _KpiGrid(cards)
    grid.resize(width, grid.height())
    grid.show()
    pump()
    try:
        assert grid.width() == width and grid.columns() == 4
        for card in cards:
            hint = card._hint  # noqa: SLF001
            assert hint.text() == hint.full_text(), f"{card.label_text()}: {hint.text()!r}"
        grid.resize(KPI_FOUR_COLUMNS_MIN - 1, grid.height())
        assert grid.columns() == 2
    finally:
        grid.hide()
        grid.deleteLater()


def test_accessibility_names(ictx: AppContext) -> None:
    seed_week(ictx)
    show(ictx)
    page = page_of(ictx)
    assert page.range_control.accessibleName() == "Range"
    assert page.export_button.accessibleName() == "Export"
    assert page.kpi_focus.accessibleName() == "Focus time"
    assert page.kpi_focus.accessibleDescription().startswith("3h 45m, up 88% vs previous 7 days")
    assert page.heatmap.accessibleName() == "Focus by weekday and hour"
    assert page.recent_list.accessibleName() == "Recent sessions"


def test_seeded_demo_renders_all_ranges(ictx: AppContext) -> None:
    seed = load_seed_demo()
    seed.seed_demo(ictx.repo, ictx.clock.now(), weeks=14)
    show(ictx)
    page = page_of(ictx)
    for rk in an.RANGES:
        page.set_range(rk)
        snap = page.snapshot()
        assert snap is not None and snap.session_count > 0
        assert page.heatmap.has_enough_data()
        assert page.top_list.items()
        assert page.recent_list.rows
        page.widget().grab()


def test_snapshot_loader_excludes_ids(ictx: AppContext) -> None:
    ids = seed_week(ictx)
    snap = kit.load_snapshot(ictx, "7d", {ids["mon"]})
    assert snap.focus_seconds == 135 * 60
    assert ids["mon"] not in [s.id for s in snap.recent]
    assert kit.load_snapshot(ictx, "bogus").range_key == "7d"


def test_snapshot_streak_is_all_time_without_loading_all_sessions(ictx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """The goal streak covers the whole history (a best streak may predate the range), but a
    refresh only loads the sessions of the ranges it shows — the history is summed in SQL."""
    from madhura.core.streaks import day_streak

    ictx.settings.update(daily_goal_minutes=60)
    old = date(2025, 3, 3)  # a 10-day run long before any range (and the idle header's 120 days)
    for i in range(10):
        add_session(ictx, datetime.combine(old + timedelta(days=i), datetime.min.time()).replace(hour=9), 61)
    late = add_session(ictx, datetime(2026, 10, 14, 2, 30), 60)  # before 04:00 → counts for Tue 13
    for back in (0, 2):  # Wed 14, Mon 12
        add_session(ictx, datetime(2026, 10, 14 - back, 9, 0), 60)
    add_session(ictx, datetime(2026, 10, 11, 9, 0), 90, "abandoned")  # never counts
    starts: list[datetime] = []
    real = ictx.repo.sessions_between

    def spy(start: datetime, end: datetime, *a, **kw):  # type: ignore[no-untyped-def]
        starts.append(start)
        return real(start, end, *a, **kw)

    monkeypatch.setattr(ictx.repo, "sessions_between", spy)
    for rk in an.RANGES:
        snap = kit.load_snapshot(ictx, rk)
        prev_start, _ = an.period_bounds(an.previous_range_period(rk, TODAY), ictx.day_start_hour())
        assert starts and min(starts) >= prev_start, rk
        starts.clear()
        everything = real(datetime(1970, 1, 1), datetime(2026, 10, 15, 4, 0))
        assert snap.streak == day_streak(an.goal_dates(everything, 60, 4), TODAY, int(ictx.settings.get("work_days")))
        assert snap.streak == (3, 10)
    # Pending undo-deletes are left out of the streak too: without the 02:30 session Tue 13 is unmet.
    assert kit.load_snapshot(ictx, "7d", {late}).streak == (1, 10)


def test_kit_formatting_helpers() -> None:
    assert kit.sessions_word(1) == "1 session" and kit.sessions_word(0) == "0 sessions"
    assert kit.fmt_pct(0.845) == "84%" and kit.fmt_pct(None) == "—"
    assert kit.quote("  a   b ") == "‘a b’" and kit.quote("x" * 50, 10) == "‘xxxxxxxxx…’"
    assert kit.work_day_count(31) == 5 and kit.work_day_count(0) == 0 and kit.work_day_count(127) == 7
    assert kit.bar_label("7d", TODAY, 6, 7) == "Wed"
    assert kit.bar_label("30d", TODAY, 29, 30) == "14 Oct" and kit.bar_label("30d", TODAY, 28, 30) == ""
    assert kit.bar_label("12w", date(2026, 10, 12), 11, 12) == "12 Oct"
    assert kit.bar_title("12w", date(2026, 10, 12)) == "Week of 12 Oct" and kit.bar_title("7d", TODAY) == "Wed 14 Oct"
    assert kit.bar_detail(0, 0) == "No focus" and kit.bar_detail(8100, 3) == "2h 15m · 3 sessions"
    assert kit.fmt_session_when(datetime(2026, 10, 14, 14, 5), TODAY, 4, False) == "Today, 2:05 PM"
    assert kit.fmt_session_when(datetime(2026, 10, 15, 1, 0), TODAY, 4, True) == "Today, 01:00"
    assert kit.fmt_session_when(datetime(2026, 10, 1, 9, 0), TODAY, 4, True) == "Thu 1 Oct, 09:00"
