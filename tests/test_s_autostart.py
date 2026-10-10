"""[S] Launch at login: pure command/status logic (Windows + XDG) and the Linux desktop file."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from madhura.platform import autostart


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pretend to be Linux with an isolated XDG_CONFIG_HOME; returns the expected desktop file."""
    monkeypatch.setattr(autostart, "_IS_LINUX", True)
    monkeypatch.setattr(autostart, "_IS_WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "autostart" / "madhura.desktop"


# --------------------------------------------------------------------------- Windows (pure)


def test_windows_command_frozen() -> None:
    exe = r"C:\Program Files\Madhura\Madhura.exe"
    assert autostart.windows_command(exe, True) == '"C:\\Program Files\\Madhura\\Madhura.exe" --autostart'


def test_windows_command_from_source_uses_pythonw_next_to_python() -> None:
    exe = r"C:\Users\Ann\venv\Scripts\python.exe"
    assert autostart.pythonw_path(exe) == r"C:\Users\Ann\venv\Scripts\pythonw.exe"
    assert autostart.windows_command(exe, False) == '"C:\\Users\\Ann\\venv\\Scripts\\pythonw.exe" -m madhura --autostart'
    assert (
        autostart.windows_command(exe, False, use_pythonw=False)
        == '"C:\\Users\\Ann\\venv\\Scripts\\python.exe" -m madhura --autostart'
    )


@pytest.mark.parametrize(
    ("data", "disabled"),
    [
        (None, False),
        (b"", False),
        (bytes([0x02] + [0] * 11), False),
        (bytes([0x06] + [0] * 11), False),
        (bytes([0x03] + [1] * 11), True),
        (bytes([0x07] + [0] * 11), True),
    ],
)
def test_startup_approved_bytes(data: bytes | None, disabled: bool) -> None:
    assert autostart.approved_disabled(data) is disabled


def test_windows_status_matrix() -> None:
    cmd = '"C:\\Apps\\Madhura.exe" --autostart'
    off = bytes([0x03] + [0] * 11)
    on = bytes([0x02] + [0] * 11)
    assert autostart.windows_status(None, cmd, None) == "off"
    assert autostart.windows_status("", cmd, off) == "off"
    assert autostart.windows_status(cmd, cmd, None) == "on"
    assert autostart.windows_status(cmd, cmd, on) == "on"
    assert autostart.windows_status(cmd.upper(), cmd, None) == "on"  # paths are case-insensitive
    assert autostart.windows_status(cmd, cmd, off) == "disabled_by_os"
    assert autostart.windows_status('"D:\\Old\\Madhura.exe" --autostart', cmd, None) == "needs_repair"
    # Disabled in Windows wins over a stale path: the user has to act in Windows first.
    assert autostart.windows_status('"D:\\Old\\Madhura.exe" --autostart', cmd, off) == "disabled_by_os"


def test_commands_equal() -> None:
    assert autostart.commands_equal(' "A"  -m x ', '"a" -m X')
    assert not autostart.commands_equal(None, "x")
    assert not autostart.commands_equal('"a" -m x', '"b" -m x')


# --------------------------------------------------------------------------- launch commands


def test_launch_command_from_source_and_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart, "_IS_WINDOWS", False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.launch_command() == [sys.executable, "-m", "madhura", "--autostart"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.launch_command() == [sys.executable, "--autostart"]


def test_relaunch_command_drops_background_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    argv = ["/x/madhura/__main__.py", "--autostart", "--minimized", "-platform", "xcb"]
    assert autostart.relaunch_command(argv) == [sys.executable, "-m", "madhura", "-platform", "xcb"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.relaunch_command(["Madhura.exe", "--minimized"]) == [sys.executable]


# --------------------------------------------------------------------------- XDG (pure)


def test_desktop_exec_quoting() -> None:
    assert autostart.desktop_exec(["/usr/bin/python3", "-m", "madhura", "--autostart"]) == (
        "/usr/bin/python3 -m madhura --autostart"
    )
    assert autostart.desktop_exec(["/opt/My Apps/python3", "--autostart"]) == '"/opt/My Apps/python3" --autostart'
    assert autostart.desktop_exec(["/opt/100%/py"]) == "/opt/100%%/py"
    # A literal $ inside a quoted argument becomes \\$ in the file; a backslash becomes four.
    assert autostart.desktop_exec(["/a b/$x"]) == '"/a b/\\\\$x"'
    assert autostart.desktop_exec(["/a b\\c"]) == '"/a b\\\\\\\\c"'


def test_desktop_entry_and_status_roundtrip() -> None:
    argv = ["/usr/bin/python3", "-m", "madhura", "--autostart"]
    text = autostart.desktop_entry(argv, workdir="/home/ann/src/Madhura", icon="/x/icon.png")
    assert text.startswith("[Desktop Entry]\n")
    assert "Type=Application\n" in text and "Name=Madhura\n" in text
    assert "X-GNOME-Autostart-enabled=true\n" in text
    assert "Path=/home/ann/src/Madhura\n" in text and "Icon=/x/icon.png\n" in text
    expected = autostart.desktop_exec(argv)
    assert autostart.desktop_status(text, expected) == "on"
    assert autostart.desktop_status(None, expected) == "off"
    assert autostart.desktop_status(text, "/other/python -m madhura --autostart") == "needs_repair"
    assert autostart.desktop_status(text.replace("enabled=true", "enabled=false"), expected) == "disabled_by_os"
    assert autostart.desktop_status(text + "Hidden=true\n", expected) == "disabled_by_os"
    # Keys outside [Desktop Entry] are ignored.
    other = text + "[Desktop Action x]\nExec=/nope\nHidden=true\n"
    assert autostart.desktop_status(other, expected) == "on"


# --------------------------------------------------------------------------- Linux (file)


def test_linux_enable_disable_roundtrip(linux: Path) -> None:
    assert autostart.is_supported()
    assert autostart.desktop_file_path() == linux
    assert autostart.status() == "off" and autostart.is_enabled() is False
    assert autostart.set_enabled(True) is True
    assert linux.is_file()
    text = linux.read_text(encoding="utf-8")
    assert f"Exec={autostart.desktop_exec(autostart.launch_command())}" in text
    assert "X-GNOME-Autostart-enabled=true" in text
    assert autostart.status() == "on" and autostart.is_enabled() is True
    assert not list(linux.parent.glob("*.tmp"))
    assert autostart.set_enabled(False) is True
    assert not linux.exists()
    assert autostart.status() == "off"
    assert autostart.set_enabled(False) is True  # already off: still fine


def test_linux_status_detects_repair_and_os_disable(linux: Path) -> None:
    autostart.set_enabled(True)
    text = linux.read_text(encoding="utf-8")
    linux.write_text(text.replace("X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-enabled=false"), encoding="utf-8")
    assert autostart.status() == "disabled_by_os"
    assert autostart.is_enabled() is False
    linux.write_text(text.replace("-m madhura", "-m old_madhura"), encoding="utf-8")
    assert autostart.status() == "needs_repair"
    assert autostart.is_enabled() is True
    assert autostart.set_enabled(True) is True  # repair rewrites the entry
    assert autostart.status() == "on"


def test_xdg_config_home_must_be_absolute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    assert autostart.desktop_file_path() == Path.home() / ".config" / "autostart" / "madhura.desktop"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert autostart.desktop_file_path() == Path.home() / ".config" / "autostart" / "madhura.desktop"


def test_linux_write_failure_returns_false(linux: Path) -> None:
    linux.parent.parent.mkdir(parents=True)
    linux.parent.write_text("not a folder", encoding="utf-8")  # autostart/ is a file → mkdir fails
    assert autostart.set_enabled(True) is False


def test_unsupported_platform_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart, "_IS_LINUX", False)
    monkeypatch.setattr(autostart, "_IS_WINDOWS", False)
    assert autostart.is_supported() is False
    assert autostart.status() == "off"
    assert autostart.is_enabled() is False
    assert autostart.set_enabled(True) is False


def test_status_never_raises(linux: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(autostart, "_linux_status", boom)
    monkeypatch.setattr(autostart, "_linux_set", lambda _on: boom())
    assert autostart.status() == "off"
    assert autostart.set_enabled(True) is False


def test_windows_registry_helpers_are_inert_off_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("real registry")
    assert autostart._winreg() is None  # noqa: SLF001
    assert autostart._read_value(autostart.RUN_KEY) is None  # noqa: SLF001
    assert autostart._windows_set(True) is False  # noqa: SLF001
