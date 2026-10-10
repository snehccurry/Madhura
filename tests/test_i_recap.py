"""WeeklyRecapCard [I]: numbers, copy, should_show / kv 'weekly_recap_seen', dismiss, Focus page embedding."""

# ruff: noqa: F811 - pytest fixtures are imported from test_i_support and used as parameters
from __future__ import annotations

from datetime import date, datetime

from madhura.context import AppContext
from madhura.ui import theme
from madhura.ui.widgets import recap as recap_mod
from madhura.ui.widgets.recap import SEEN_KEY, RecapData, WeeklyRecapCard, compute_recap, recap_text, week_key
from test_i_support import add_session, ictx, no_summary, pump, show, win_of  # noqa: F401


def seed_last_week(ctx: AppContext) -> None:
    """Week of Mon 5 Oct (last week, as of Wed 14 Oct) + the week before."""
    add_session(ctx, datetime(2026, 10, 6, 9, 0), 120, project="Madhura",
                checkins=("focused", "focused", "focused", ("drifted", "Phone")))
    add_session(ctx, datetime(2026, 10, 8, 9, 0), 180, project="Thesis")
    add_session(ctx, datetime(2026, 10, 12, 2, 0), 0.5, task="Late night")  # Mon 02:00 → logical Sunday 11 Oct
    add_session(ctx, datetime(2026, 10, 9, 9, 0), 300, "abandoned", project="Other")
    add_session(ctx, datetime(2026, 9, 30, 9, 0), 150)


def test_week_key() -> None:
    assert week_key(date(2026, 10, 14)) == "2026-W42"
    assert week_key(date(2027, 1, 1)) == "2026-W53"
    assert week_key(date(2026, 1, 5)) == "2026-W02"


def test_compute_recap_numbers(ictx: AppContext) -> None:
    seed_last_week(ictx)
    data = compute_recap(ictx)
    assert data is not None
    assert data.week_start == date(2026, 10, 5)
    assert data.seconds == 300 * 60 + 30
    assert data.delta is not None and round(data.delta * 100) == 100
    assert data.best_day == date(2026, 10, 8)
    assert data.top_project == "Thesis"
    assert data.score == 0.75


def test_recap_text_variants() -> None:
    full = RecapData(date(2026, 10, 5), 11 * 3600 + 20 * 60, 0.18, date(2026, 10, 8), "Madhura", 0.84)
    assert recap_text(full) == "Last week: 11h 20m (+18%) · best day Thu · top project Madhura · 84% focused"
    down = RecapData(date(2026, 10, 5), 3600, -0.08, None, None, None)
    assert recap_text(down) == "Last week: 1h (−8%)"
    first = RecapData(date(2026, 10, 5), 1800, None, date(2026, 10, 5), None, None)
    assert recap_text(first) == "Last week: 30m · best day Mon"


def test_should_show_once_per_iso_week(ictx: AppContext) -> None:
    seed_last_week(ictx)
    card = WeeklyRecapCard(ictx)
    assert card.should_show() is True
    assert ictx.repo.kv_get(SEEN_KEY) is None  # asking does not consume it
    assert card.text() == "Last week: 5h (+100%) · best day Thu · top project Thesis · 75% focused"
    card.show()  # actually seen
    pump()
    assert ictx.repo.kv_get(SEEN_KEY) == "2026-W42"
    assert card.should_show() is False
    card.hide()
    # Next ISO week, with focus in the week of 12 Oct → shows again.
    add_session(ictx, datetime(2026, 10, 14, 8, 0), 45)
    ictx.clock.set(datetime(2026, 10, 19, 9, 0))  # type: ignore[attr-defined]
    assert card.should_show() is True
    assert card.data() is not None and card.data().week_start == date(2026, 10, 12)  # type: ignore[union-attr]
    assert card.text().startswith("Last week: 45m")
    card.deleteLater()


def test_no_recap_without_counted_focus_last_week(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 7, 9, 0), 60, "abandoned")
    add_session(ictx, datetime(2026, 10, 13, 9, 0), 60)  # this week only
    card = WeeklyRecapCard(ictx)
    assert card.data() is None and card.should_show() is False and card.text() == ""
    card.deleteLater()


def test_logical_day_monday_night_counts_for_last_week(ictx: AppContext) -> None:
    add_session(ictx, datetime(2026, 10, 12, 3, 0), 40)  # Mon 03:00 < day start 04:00 → Sunday
    card = WeeklyRecapCard(ictx)
    assert card.should_show() is True
    assert card.data().best_day == date(2026, 10, 11)  # type: ignore[union-attr]
    ictx.settings.update(day_start_hour=0)
    card.refresh()
    assert card.data() is None  # now it is this week's Monday
    card.deleteLater()


def test_dismiss_remembers_week_and_emits(ictx: AppContext) -> None:
    seed_last_week(ictx)
    card = WeeklyRecapCard(ictx)
    card.show()
    fired: list[bool] = []
    card.dismissed.connect(lambda: fired.append(True))
    card.close_button.click()
    assert fired == [True] and card.isHidden()
    assert ictx.repo.kv_get(SEEN_KEY) == "2026-W42"
    assert card.close_button.accessibleName() == "Dismiss weekly recap"
    card.deleteLater()


def test_see_insights_navigates(ictx: AppContext) -> None:
    seed_last_week(ictx)
    show(ictx, page="focus")
    card = WeeklyRecapCard(ictx)
    card.see_button.click()
    pump()
    assert win_of(ictx).current_page() == "insights"
    card.deleteLater()


def test_rich_text_follows_theme(ictx: AppContext) -> None:
    seed_last_week(ictx)
    card = WeeklyRecapCard(ictx)
    dark_success = theme.palette().success
    assert dark_success in card.label.text()
    ictx.settings.update(theme="light")
    pump()
    assert theme.palette().success in card.label.text() and dark_success not in card.label.text()
    assert card.accessibleName() == "Weekly recap"
    assert card.accessibleDescription() == card.text()
    card.deleteLater()


def test_embedded_on_focus_page_and_marked_seen(ictx: AppContext) -> None:
    seed_last_week(ictx)
    show(ictx, page="focus")
    idle = win_of(ictx).pages["focus"].idle_view  # type: ignore[attr-defined]
    idle.refresh()
    pump()
    assert isinstance(idle.recap, WeeklyRecapCard)
    assert idle.recap.isVisible()
    assert ictx.repo.kv_get(SEEN_KEY) == "2026-W42"
    idle.recap.dismiss()
    pump()
    assert not idle.recap.isVisible()
    # A fresh card in the same week stays hidden.
    assert WeeklyRecapCard(ictx).should_show() is False


def test_module_exports() -> None:
    assert recap_mod.SEEN_KEY == "weekly_recap_seen"
