from __future__ import annotations

import sys

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
