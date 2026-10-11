"""[O] One-shot popups must not pile up: per-click menus, the emoji popover, toast animations."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QApplication, QMenu
from shiboken6 import isValid
from test_o_support import octx  # noqa: F401
from test_shell_support import shell  # noqa: F401

from madhura.context import AppContext
from madhura.ui import theme
from madhura.ui.widgets.menu import Menu
from madhura.ui.widgets.popover import Popover


def flush_deletes() -> None:
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def live_menus(root: QObject) -> int:
    return len(root.findChildren(QMenu))


def close_popups() -> None:
    for _ in range(10):
        popup = QApplication.activePopupWidget()
        if popup is None:
            break
        popup.close()
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMenu) and w.isVisible():
            w.close()


def test_one_shot_menu_is_deleted_once_closed_but_not_before_its_action(shell: AppContext) -> None:  # noqa: F811
    window: Any = shell.window
    seen: list[bool] = []
    menu = Menu(window, delete_on_hide=True)
    menu.add_action("Go", lambda: seen.append(isValid(menu)))
    act = menu.actions()[0]
    menu.popup(QPoint(40, 40))
    menu.hide()  # QMenu hides itself before it triggers the chosen action
    act.trigger()
    assert seen == [True]
    flush_deletes()
    assert not isValid(menu)

    keep = Menu(window)  # the default (the tray's persistent menu) survives closing
    keep.add_action("Stay")
    keep.popup(QPoint(40, 40))
    keep.hide()
    flush_deletes()
    assert isValid(keep)
    keep.deleteLater()


def test_pill_right_click_menus_do_not_pile_up(octx: AppContext) -> None:  # noqa: F811
    octx.engine.start("Write", 60)
    pc = octx.pill
    flush_deletes()
    tops = len(QApplication.topLevelWidgets())
    for _ in range(12):
        pc.show_menu(QPoint(50, 50))
        menu = pc._menu  # noqa: SLF001
        assert menu is not None and menu.isVisible()
        menu.close()
    flush_deletes()
    assert live_menus(pc.pill) == 0
    assert len(QApplication.topLevelWidgets()) == tops
    pc.show_menu(QPoint(50, 50))  # still works after the old ones are gone
    menu = pc._menu  # noqa: SLF001
    pause = next(a for a in menu.actions() if a.text() == "Pause")
    menu.hide()
    pause.trigger()
    assert str(octx.engine.state) == "paused"
    flush_deletes()
    assert live_menus(pc.pill) == 0


def test_page_menus_do_not_pile_up(shell: AppContext) -> None:  # noqa: F811
    ctx = shell
    window: Any = ctx.window
    window.resize(1120, 760)
    window.show_main(activate=True)
    flush_deletes()
    focus = window.pages["focus"]
    idle = focus.idle_view
    tid = idle.up_next.add_task("Outline the review", 25)
    hid = ctx.repo.add_habit("Stretch")
    habits = window.pages["habits"]
    insights = window.pages["insights"]
    window.show_page("habits")
    habits.refresh()
    window.show_page("insights")
    window.show_page("focus")
    flush_deletes()
    before = live_menus(window)
    openers = [
        lambda: idle.up_next.open_menu(tid),
        lambda: idle._open_snooze_menu(),  # noqa: SLF001
        lambda: focus._open_paused_snooze(),  # noqa: SLF001
        lambda: focus.active_view._open_more_menu(),  # noqa: SLF001
        lambda: habits.open_menu(hid),
        insights.open_export_menu,
    ]
    for _ in range(3):
        for open_menu in openers:
            open_menu()
            QApplication.processEvents()
            assert live_menus(window) > before  # it did open a menu
            close_popups()
    flush_deletes()
    assert live_menus(window) == before


def test_emoji_popovers_do_not_pile_up(shell: AppContext) -> None:  # noqa: F811
    from madhura.ui.pages.habit_editor import HabitEditorSheet

    window: Any = shell.window
    window.resize(1120, 760)
    window.show_main(activate=True)
    sheet = HabitEditorSheet(shell)
    window.show_sheet(sheet)
    flush_deletes()
    before = len(window.findChildren(Popover))
    for _ in range(5):
        sheet.open_emoji_picker()
        pop = sheet.emoji_popover()
        assert pop is not None and pop.is_open()
        pop.dismiss()
        assert sheet.emoji_popover() is None
    flush_deletes()
    assert len(window.findChildren(Popover)) == before
    sheet.open_emoji_picker()  # opens fine afterwards, picking still works
    sheet.emoji_popover().content().buttons[0].click()
    assert sheet.emoji() and sheet.emoji_popover() is None


def test_toast_slide_animations_are_deleted(shell: AppContext, qtbot: Any) -> None:  # noqa: F811
    window: Any = shell.window
    window.resize(1120, 760)
    window.show_main(activate=True)
    theme.current().set_reduce_motion(False)
    host = window.toasts
    direct = Qt.FindChildOption.FindDirectChildrenOnly
    for i in range(6):
        host.show(f"Toast {i}")
        QApplication.processEvents()
    qtbot.wait(450)  # the last 200 ms slide finishes
    flush_deletes()
    assert host.findChildren(QPropertyAnimation, options=direct) == []
    host.show("One more")  # replacing / ending a toast after its animation was deleted is fine
    host._end(expired=False, animate=False)  # noqa: SLF001
    theme.current().set_reduce_motion(True)


def test_tray_menu_stays_alive(qapp: QApplication) -> None:
    """The tray's parentless context menu is reused for the app's lifetime: it must not delete itself."""
    menu = Menu(None)
    menu.add_action("Open Madhura")
    menu.popup(QPoint(10, 10))
    menu.hide()
    flush_deletes()
    assert isValid(menu)
    menu.popup(QPoint(10, 10))
    assert menu.isVisible()
    menu.hide()
    menu.deleteLater()
    flush_deletes()
