"""[S] Launch at login: pure command/status logic (Windows + XDG) and the Linux desktop file."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from madhura import __version__, paths
from madhura.platform import autostart


def _split_windows(cmd: str) -> list[str]:
    """Split a command line the way the MSVC runtime builds argv (what pythonw.exe will see)."""
    assert cmd.startswith('"')
    end = cmd.index('"', 1)
    args, rest = [cmd[1:end]], cmd[end + 1 :]
    i, cur, in_arg, quoted = 0, "", False, False
    while i < len(rest):
        ch = rest[i]
        if ch == "\\":
            n = len(rest[i:]) - len(rest[i:].lstrip("\\"))
            i += n
            if i < len(rest) and rest[i] == '"':
                cur += "\\" * (n // 2)
                if n % 2:
                    cur += '"'
                    i += 1
            else:
                cur += "\\" * n
            in_arg = True
            continue
        if ch == '"':
            quoted, in_arg = not quoted, True
        elif ch in " \t" and not quoted:
            if in_arg:
                args.append(cur)
            cur, in_arg = "", False
        else:
            cur, in_arg = cur + ch, True
        i += 1
    if in_arg:
        args.append(cur)
    return args


class _Key:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self) -> _Key:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_winreg(store: dict[str, dict[str, Any]]) -> SimpleNamespace:
    """Just enough of ``winreg`` (HKCU only) for the Run / StartupApproved keys."""

    def open_key(_root: int, path: str, _reserved: int = 0, _access: int = 0) -> _Key:
        if path not in store:
            raise FileNotFoundError(path)
        return _Key(path)

    def create_key(_root: int, path: str, _reserved: int = 0, _access: int = 0) -> _Key:
        store.setdefault(path, {})
        return _Key(path)

    def query(key: _Key, name: str) -> tuple[Any, int]:
        if name not in store[key.path]:
            raise FileNotFoundError(name)
        return store[key.path][name], 1

    def set_value(key: _Key, name: str, _reserved: int, _kind: int, value: Any) -> None:
        store[key.path][name] = value

    def delete_value(key: _Key, name: str) -> None:
        if store[key.path].pop(name, None) is None:
            raise FileNotFoundError(name)

    return SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_READ=0x20019,
        KEY_SET_VALUE=0x0002,
        REG_SZ=1,
        OpenKey=open_key,
        CreateKeyEx=create_key,
        QueryValueEx=query,
        SetValueEx=set_value,
        DeleteValue=delete_value,
    )


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
    "root",
    [r"C:\Users\Ann\src\Madhura", r"C:\Users\O'Brien\My Code\Madhura", "D:\\", r"\\server\share\Madhura"],
)
def test_windows_command_from_checkout_passes_the_folder_as_an_argument(root: str) -> None:
    exe = r"C:\Users\Ann\venv\Scripts\python.exe"
    cmd = autostart.windows_command(exe, False, source_root=root)
    pythonw = r"C:\Users\Ann\venv\Scripts\pythonw.exe"
    # The folder is its own argument (a quote or space in it can't break the Python source), and a
    # trailing backslash (a drive root) doesn't swallow the closing quote.
    assert _split_windows(cmd) == [pythonw, "-c", autostart.SOURCE_BOOTSTRAP, root, "--autostart"]
    assert '"' not in autostart.SOURCE_BOOTSTRAP and "\\" not in autostart.SOURCE_BOOTSTRAP
    assert _split_windows(autostart.windows_command(exe, False)) == [pythonw, "-m", "madhura", "--autostart"]


def test_windows_checkout_command_stays_short() -> None:
    # Windows documents Run-key command lines as at most 260 characters.
    exe = r"C:\Users\Annabelle\AppData\Local\Programs\Python\Python313\python.exe"
    cmd = autostart.windows_command(exe, False, source_root=r"C:\Users\Annabelle\Documents\GitHub\Madhura")
    assert len(cmd) <= 240


def test_windows_source_checkout_registers_a_command_that_finds_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run-key entries start in System32, so ``-m madhura`` alone can't find a checkout."""
    store: dict[str, dict[str, Any]] = {}
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(store))
    monkeypatch.setattr(autostart, "_IS_WINDOWS", True)
    monkeypatch.setattr(autostart, "_IS_LINUX", False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    exe = r"C:\Users\Ann\venv\Scripts\python.exe"  # no pythonw.exe on this disk → python.exe
    root = r"C:\Users\Ann\src\Madhura"
    monkeypatch.setattr(sys, "executable", exe)
    monkeypatch.setattr(autostart, "_source_root", lambda: root)

    # An entry from an earlier version (plain "-m madhura") never started: offer Repair.
    store[autostart.RUN_KEY] = {"Madhura": f'"{exe}" -m madhura --autostart'}
    assert autostart.status() == "needs_repair"
    assert autostart.set_enabled(True) is True
    value = store[autostart.RUN_KEY]["Madhura"]
    assert _split_windows(value) == [exe, "-c", autostart.SOURCE_BOOTSTRAP, root, "--autostart"]
    assert autostart.status() == "on"
    assert autostart.launch_command() == [exe, "-c", autostart.SOURCE_BOOTSTRAP, root, "--autostart"]

    # Installed into site-packages: the plain form works from any folder and is what we expect.
    monkeypatch.setattr(autostart, "_source_root", lambda: None)
    assert autostart.status() == "needs_repair"
    assert autostart.set_enabled(True) is True
    assert store[autostart.RUN_KEY]["Madhura"] == f'"{exe}" -m madhura --autostart'
    assert autostart.status() == "on"
    assert autostart.set_enabled(False) is True
    assert "Madhura" not in store[autostart.RUN_KEY] and autostart.status() == "off"


def test_source_root_is_the_checkout_unless_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import site

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(site, "ENABLE_USER_SITE", False)
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(tmp_path)])
    assert autostart._source_root() == str(paths.repo_root())  # noqa: SLF001
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(tmp_path), str(paths.repo_root())])
    assert autostart._source_root() is None  # noqa: SLF001
    monkeypatch.setattr(site, "getsitepackages", lambda: [])
    monkeypatch.setattr(site, "ENABLE_USER_SITE", True)
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(paths.repo_root()))
    assert autostart._source_root() is None  # noqa: SLF001
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(tmp_path)])
    assert autostart._source_root() is None  # noqa: SLF001


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
    monkeypatch.setattr(autostart, "_source_root", lambda: None)
    assert autostart.relaunch_command(argv) == [sys.executable, "-m", "madhura", "-platform", "xcb"]
    monkeypatch.setattr(autostart, "_source_root", lambda: "/src/Madhura")
    assert autostart.relaunch_command(argv) == [
        sys.executable, "-c", autostart.SOURCE_BOOTSTRAP, "/src/Madhura", "-platform", "xcb",
    ]  # fmt: skip
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.relaunch_command(["Madhura.exe", "--minimized"]) == [sys.executable]


def test_relaunch_command_starts_madhura_from_any_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Started at login on Windows the current folder is System32; the relaunch must still work."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    cmd = autostart.relaunch_command(["madhura", "--autostart", "--version"])
    done = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"Madhura {__version__}"


def test_prepare_relaunch_environment_starts_a_frozen_build_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    pyi = {
        "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEIabc",
        "_PYI_PARENT_PROCESS_LEVEL": "1",
        "_PYI_ARCHIVE_FILE": "/opt/Madhura",
        "_MEIPASS2": "/tmp/_MEIabc",  # PyInstaller < 6.10
    }
    env = {"PATH": "/usr/bin", "HOME": "/home/ann", **pyi}
    monkeypatch.delattr(sys, "frozen", raising=False)
    autostart.prepare_relaunch_environment(env)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/ann", **pyi}  # from source: untouched
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    autostart.prepare_relaunch_environment(env)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/ann", "PYINSTALLER_RESET_ENVIRONMENT": "1"}


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
