from __future__ import annotations

import sys
from pathlib import Path

import pytest

from madhura import platform as mplatform
from madhura.platform import attention, idle, windows


def test_platform_flags_are_exclusive() -> None:
    assert [mplatform.IS_WINDOWS, mplatform.IS_MAC, mplatform.IS_LINUX].count(True) <= 1
    assert mplatform.IS_WINDOWS == (sys.platform == "win32")


def test_idle_wrap_math() -> None:
    assert idle.idle_ms_from_ticks(5000, 2000) == 3000
    # GetTickCount wrapped after 49.7 days: now small, last input near 2**32
    assert idle.idle_ms_from_ticks(1000, 0xFFFFFF00) == 1000 + 0x100
    assert idle.idle_ms_from_ticks(0, 0) == 0


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="non-Windows/macOS fallback")
def test_idle_unsupported_returns_none() -> None:
    assert idle.get_idle_seconds() is None


def test_attention_mapping_and_fallback() -> None:
    assert attention.attention_from_quns(1) == "away"
    assert attention.attention_from_quns(2) == "busy"
    assert attention.attention_from_quns(3) == "busy"
    assert attention.attention_from_quns(4) == "busy"
    assert attention.attention_from_quns(5) == "available"
    assert attention.attention_from_quns(6) == "available"
    assert attention.attention_from_quns(7) == "busy"
    assert attention.attention_from_quns(99) == "available"
    if sys.platform != "win32":
        assert attention.system_attention() == "available"


def test_colorref() -> None:
    assert windows.colorref("#112233") == 0x332211
    assert windows.colorref((1, 2, 3)) == 0x030201


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows fallbacks")
def test_windows_helpers_are_noops_elsewhere(qtbot) -> None:
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    qtbot.addWidget(w)
    assert windows.set_app_user_model_id() is False
    assert windows.register_app_user_model_id() is False
    assert windows.set_caption_colors(w, "#000000", "#ffffff", "#333333") is False
    assert windows.set_round_corners(w) is False
    assert windows.set_dark_title_bar(w, True) is False
    assert windows.reassert_topmost(w) is False
    assert windows.allow_set_foreground(1234) is False
    assert windows.client_animations_enabled() is True
    assert windows.windows_build() is None
    assert windows.is_windows_11() is False
    assert windows.flash(w) is True
    windows.bring_to_front(w, steal_focus=False)
    assert w.isVisible()
    w.showMinimized()
    windows.bring_to_front(w, steal_focus=True)
    assert w.isVisible()


# --------------------------------------------------------------------------- bring_to_front


def test_bring_to_front_keeps_maximized_when_unminimizing(qtbot) -> None:
    """showNormal() would drop Maximized too: a maximized-then-minimized window must come back maximized."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    qtbot.addWidget(w)
    w.show()
    w.setWindowState(Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized)
    assert w.isMinimized()
    windows.bring_to_front(w, steal_focus=False)
    assert not w.isMinimized()
    assert w.windowState() & Qt.WindowState.WindowMaximized


def test_bring_to_front_shows_without_activating_only_for_that_show(qtbot) -> None:
    """The no-activate request is scoped to bring_to_front's own show (Qt never clears
    WA_ShowWithoutActivating's copy, which would make show_main(activate=True) inactive too)."""
    from PySide6.QtWidgets import QWidget

    seen: list[object] = []

    class Probe(QWidget):
        def showEvent(self, event) -> None:  # noqa: ANN001, N802
            handle = self.windowHandle()
            seen.append(handle.property("_q_showWithoutActivating") if handle is not None else "no-handle")
            super().showEvent(event)

    w = Probe()
    qtbot.addWidget(w)
    windows.bring_to_front(w, steal_focus=False)  # never shown before: no native window yet
    assert w.isVisible() and seen == [True]
    assert w.windowHandle().property("_q_showWithoutActivating") is None
    w.hide()
    windows.bring_to_front(w, steal_focus=True)
    assert seen == [True, None]
    from PySide6.QtCore import Qt

    assert not w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def _fake_windows(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[int, int, int]], list[object]]:
    calls: list[tuple[int, int, int]] = []
    native: list[object] = []
    monkeypatch.setattr(windows, "_IS_WINDOWS", True)
    monkeypatch.setattr(windows, "_TOPMOST_RELEASE_MS", 10)
    monkeypatch.setattr(windows, "_hwnd", lambda w: 4242)
    monkeypatch.setattr(windows, "_set_window_pos", lambda hwnd, after, flags: calls.append((hwnd, after, flags)) or True)
    monkeypatch.setattr(windows, "_show_no_activate_native", lambda w: native.append(w) or True)
    return calls, native


def test_bring_to_front_topmost_release_never_reshows(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The NOTOPMOST release must not carry SWP_SHOWWINDOW: a window hidden meanwhile would reappear
    natively while Qt still thinks it is hidden."""
    from PySide6.QtWidgets import QWidget

    calls, _native = _fake_windows(monkeypatch)
    w = QWidget()
    qtbot.addWidget(w)
    windows.bring_to_front(w, steal_focus=False)
    assert calls == [(4242, windows.HWND_TOPMOST, windows.SWP_NOMOVE | windows.SWP_NOSIZE | windows.SWP_NOACTIVATE | windows.SWP_SHOWWINDOW)]
    w.hide()  # the user closes it to the tray within 1.5 s
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=2000)
    _hwnd, after, flags = calls[1]
    assert after == windows.HWND_NOTOPMOST and not flags & windows.SWP_SHOWWINDOW
    assert flags & windows.SWP_NOACTIVATE and not w.isVisible()

    # A window deleted before the release: the timer is bound to it, so nothing runs.
    from shiboken6 import delete

    gone = QWidget()
    windows.bring_to_front(gone, steal_focus=False)
    delete(gone)
    qtbot.wait(50)
    assert len(calls) == 3


def test_bring_to_front_windows_restores_minimized_without_activation(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    _calls, native = _fake_windows(monkeypatch)
    w = QWidget()
    qtbot.addWidget(w)
    w.show()
    w.setWindowState(Qt.WindowState.WindowMinimized)
    windows.bring_to_front(w, steal_focus=False)
    assert native == [w]  # ShowWindow(SW_SHOWNOACTIVATE); Qt syncs from WM_SIZE
    # Maximized before minimizing: SW_SHOWNOACTIVATE would restore the normal rect → Qt path.
    m = QWidget()
    qtbot.addWidget(m)
    m.show()
    m.setWindowState(Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized)
    windows.bring_to_front(m, steal_focus=False)
    assert native == [w]
    assert not m.isMinimized() and m.windowState() & Qt.WindowState.WindowMaximized


def test_register_app_user_model_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Windows needs HKCU\\Software\\Classes\\AppUserModelId\\<id> to show "Madhura" (not the raw ID)."""
    import types

    store: dict[str, dict[str, tuple[str, int]]] = {}
    writes: list[str] = []

    class Key:
        def __init__(self, path: str) -> None:
            self.values = store.setdefault(path, {})

        def __enter__(self) -> Key:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def query(key: Key, name: str) -> tuple[str, int]:
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def set_value(key: Key, name: str, _reserved: int, kind: int, value: str) -> None:
        writes.append(name)
        key.values[name] = (value, kind)

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_READ=0x20019,
        KEY_WRITE=0x20006,
        REG_SZ=1,
        CreateKeyEx=lambda root, path, reserved, access: Key(path),
        QueryValueEx=query,
        SetValueEx=set_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(windows, "_IS_WINDOWS", True)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"png")
    assert windows.register_app_user_model_id("Madhura.FocusApp", "Madhura", icon) is True
    values = store["Software\\Classes\\AppUserModelId\\Madhura.FocusApp"]
    assert values == {"DisplayName": ("Madhura", 1), "IconUri": (str(icon), 1)}
    writes.clear()
    assert windows.register_app_user_model_id("Madhura.FocusApp", "Madhura", tmp_path / "missing.png") is True
    assert writes == []  # unchanged values are not rewritten; a missing icon is skipped
    fake.CreateKeyEx = lambda *a: (_ for _ in ()).throw(PermissionError("denied"))
    assert windows.register_app_user_model_id() is False  # never raises


# --------------------------------------------------------------------------- probe failures reach the log


def test_idle_probe_failures_are_logged_at_warning_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="madhura.platform.idle")
    monkeypatch.setattr(idle, "_WARNED", set())
    monkeypatch.setattr(idle._Cache, "resolved", False)
    monkeypatch.setattr(idle._Cache, "fn", None)
    monkeypatch.setattr(idle.sys, "platform", "win32")

    def no_user32() -> None:
        raise AttributeError("module 'ctypes' has no attribute 'windll'")

    monkeypatch.setattr(idle, "_windows_fn", no_user32)
    assert idle.get_idle_seconds() is None
    assert idle.get_idle_seconds() is None  # resolution is cached: logged once
    resolve = [r for r in caplog.records if "Idle-time API unavailable" in r.getMessage()]
    assert [r.levelno for r in resolve] == [logging.WARNING] and resolve[0].exc_info is not None

    def failing_query() -> float:
        raise OSError("GetLastInputInfo failed")

    monkeypatch.setattr(idle._Cache, "fn", failing_query)
    for _ in range(3):
        assert idle.get_idle_seconds() is None
    query = [r.levelno for r in caplog.records if r.getMessage() == "Idle-time query failed"]
    assert query == [logging.WARNING, logging.DEBUG, logging.DEBUG]


def test_system_attention_failure_is_logged_at_warning_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    if sys.platform == "win32":
        pytest.skip("needs a platform without shell32")
    caplog.set_level(logging.DEBUG, logger="madhura.platform.attention")
    monkeypatch.setattr(attention, "_WARNED", set())
    monkeypatch.setattr(attention.sys, "platform", "win32")
    assert attention.system_attention() == "available"  # no ctypes.windll here → failure path
    assert attention.system_attention() == "available"
    levels = [r.levelno for r in caplog.records if "SHQueryUserNotificationState failed" in r.getMessage()]
    assert levels == [logging.WARNING, logging.DEBUG]
