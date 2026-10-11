"""Launch-at-login integration (spec §1.10 System, §1.11 onboarding).

* **Windows** — value ``Madhura`` under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``:
  ``"<exe>" --autostart`` when frozen; ``"<pythonw.exe next to python>" -m madhura --autostart`` when
  installed into site-packages; from a source checkout, ``"<pythonw.exe>" -c "<SOURCE_BOOTSTRAP>"
  "<checkout>" --autostart`` (Run entries start in System32, where ``-m madhura`` can't find it).
  The user can switch the entry off in Task Manager / Settings › Apps › Startup; Windows records
  that in ``…\\Explorer\\StartupApproved\\Run`` (first byte odd, e.g. ``0x03`` = disabled), which
  :func:`status` reports as ``"disabled_by_os"``.
* **Linux** — an XDG autostart entry ``$XDG_CONFIG_HOME/autostart/madhura.desktop`` (default
  ``~/.config``) with ``X-GNOME-Autostart-enabled=true``.
* **macOS** — not supported yet (:func:`is_supported` is False).

Every public function is safe on any OS and never raises; failures are logged. The pure helpers
(:func:`windows_command`, :func:`windows_status`, :func:`desktop_exec`, :func:`desktop_entry`,
:func:`desktop_status`, …) carry the logic so it can be tested anywhere.
"""

from __future__ import annotations

import logging
import ntpath
import os
import sys
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Any

from madhura import APP_NAME

__all__ = [
    "STATUSES",
    "RUN_KEY",
    "APPROVED_KEY",
    "VALUE_NAME",
    "DESKTOP_FILE_NAME",
    "is_supported",
    "is_enabled",
    "status",
    "set_enabled",
    "launch_command",
    "relaunch_command",
    "prepare_relaunch_environment",
    "SOURCE_BOOTSTRAP",
    "pythonw_path",
    "windows_command",
    "windows_status",
    "approved_disabled",
    "commands_equal",
    "desktop_file_path",
    "desktop_exec",
    "desktop_entry",
    "desktop_status",
]

log = logging.getLogger(__name__)

#: Every value :func:`status` can return.
STATUSES: tuple[str, ...] = ("on", "off", "disabled_by_os", "needs_repair")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = APP_NAME
DESKTOP_FILE_NAME = "madhura.desktop"
AUTOSTART_FLAG = "--autostart"

_IS_WINDOWS = sys.platform == "win32"
_IS_LINUX = sys.platform.startswith("linux")

#: ``python -c`` program that runs Madhura from a source checkout whatever the current folder is: the
#: checkout comes in as the first argument (never pasted into the source, so no path can break the
#: quoting) and goes first on ``sys.path``; the rest of argv is Madhura's. Kept short: Windows
#: documents Run-key command lines as at most 260 characters.
SOURCE_BOOTSTRAP = "import sys;sys.path.insert(0,sys.argv.pop(1));from madhura.app import main;sys.exit(main())"


# =========================================================================== commands


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def pythonw_path(executable: str) -> str:
    """``pythonw.exe`` in the same folder as *executable* (Windows path rules on every OS)."""
    return ntpath.join(ntpath.dirname(executable), "pythonw.exe")


def _win_quote(arg: str) -> str:
    """*arg* double-quoted for a Windows command line. Paths can't contain ``"``; trailing
    backslashes are doubled so ``C:\\`` doesn't escape the closing quote."""
    trailing = len(arg) - len(arg.rstrip("\\"))
    return '"' + arg + "\\" * trailing + '"'


def windows_command(executable: str, frozen: bool, *, use_pythonw: bool = True, source_root: str | None = None) -> str:
    """The Run-key command line.

    Frozen: ``"<exe>" --autostart``. Otherwise the interpreter (``pythonw.exe`` next to *executable*,
    or *executable* itself when ``use_pythonw`` is False, e.g. pythonw is missing) with
    ``-m madhura --autostart``, or — for a source checkout at *source_root* —
    ``-c "<SOURCE_BOOTSTRAP>" "<source_root>" --autostart``.
    """
    if frozen:
        return f'"{executable}" {AUTOSTART_FLAG}'
    interpreter = pythonw_path(executable) if use_pythonw else executable
    if source_root:
        return f'"{interpreter}" -c "{SOURCE_BOOTSTRAP}" {_win_quote(source_root)} {AUTOSTART_FLAG}'
    return f'"{interpreter}" -m madhura {AUTOSTART_FLAG}'


def _windows_interpreter() -> str:
    exe = sys.executable
    candidate = pythonw_path(exe)
    return candidate if os.path.exists(candidate) else exe


def _same_dir(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _source_root() -> str | None:
    """The folder holding the ``madhura`` package, which commands then put on ``sys.path``
    themselves; None when frozen or installed into site-packages (``-m madhura`` works anywhere).
    Anywhere else — a source checkout — ``-m madhura`` only works from inside that folder."""
    if _frozen():
        return None
    import site

    from madhura import paths

    root = paths.repo_root()
    try:
        site_dirs = list(site.getsitepackages())
        if site.ENABLE_USER_SITE:
            site_dirs.append(site.getusersitepackages())
    except AttributeError:  # pragma: no cover - very old virtualenv site.py
        site_dirs = []
    if any(_same_dir(root, d) for d in site_dirs if d):
        return None
    return str(root)


def _module_args(source_root: str | None) -> list[str]:
    """Interpreter arguments that run Madhura: ``-m madhura``, or the checkout bootstrap."""
    return ["-c", SOURCE_BOOTSTRAP, source_root] if source_root else ["-m", "madhura"]


def launch_command() -> list[str]:
    """argv used to start Madhura at login on this OS: the frozen exe, or the interpreter running
    ``-m madhura`` (Linux sets the entry's working folder instead; Windows can't, so a checkout
    there gets :data:`SOURCE_BOOTSTRAP`)."""
    if _frozen():
        return [sys.executable, AUTOSTART_FLAG]
    if _IS_WINDOWS:
        return [_windows_interpreter(), *_module_args(_source_root()), AUTOSTART_FLAG]
    return [sys.executable, "-m", "madhura", AUTOSTART_FLAG]


def relaunch_command(argv: Sequence[str] | None = None) -> list[str]:
    """argv that restarts Madhura in the foreground (e.g. after staging a restore).

    Keeps the user's extra arguments but drops ``--autostart`` / ``--minimized`` so the window
    shows after the restart. A source checkout is found whatever the current folder is (it is
    System32 when Windows started Madhura at login).
    """
    args = list(sys.argv if argv is None else argv)[1:]
    extra = [a for a in args if a not in (AUTOSTART_FLAG, "--minimized")]
    if _frozen():
        return [sys.executable, *extra]
    return [sys.executable, *_module_args(_source_root()), *extra]


def prepare_relaunch_environment(environ: MutableMapping[str, str]) -> None:
    """Let a frozen build start itself again as a fresh instance (no-op from source).

    A PyInstaller onefile child would otherwise pass its bootloader variables on, and the new
    process would reuse this one's extraction folder — which is deleted as this process exits.
    PyInstaller >= 6.10 honours ``PYINSTALLER_RESET_ENVIRONMENT``; older bootloaders look for
    ``_MEIPASS2`` / ``_PYI_*``, so those go too.
    """
    if not _frozen():
        return
    for name in [k for k in environ if k.upper() == "_MEIPASS2" or k.upper().startswith("_PYI_")]:
        environ.pop(name, None)
    environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"


def commands_equal(a: str | None, b: str | None) -> bool:
    """Compare two command lines the way Windows would (whitespace-trimmed, case-insensitive)."""
    if a is None or b is None:
        return False
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def approved_disabled(data: bytes | None) -> bool:
    """True when a StartupApproved value marks the entry disabled (odd first byte, e.g. 0x03)."""
    return bool(data) and (data[0] & 0x01) == 0x01  # type: ignore[index]


def windows_status(run_value: str | None, expected: str, approved: bytes | None) -> str:
    """Status from the Run value, the command we would write and the StartupApproved bytes."""
    if not run_value:
        return "off"
    if approved_disabled(approved):
        return "disabled_by_os"
    if not commands_equal(run_value, expected):
        return "needs_repair"
    return "on"


# =========================================================================== Windows registry


def _winreg() -> Any:
    if not _IS_WINDOWS:
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - only on exotic Windows builds
        return None
    return winreg


def _read_value(subkey: str) -> Any:
    reg = _winreg()
    if reg is None:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, subkey, 0, reg.KEY_READ) as key:
            value, _kind = reg.QueryValueEx(key, VALUE_NAME)
            return value
    except OSError:
        return None


def _expected_windows_command() -> str:
    exe = sys.executable
    if _frozen():
        return windows_command(exe, True)
    return windows_command(exe, False, use_pythonw=os.path.exists(pythonw_path(exe)), source_root=_source_root())


def _windows_status() -> str:
    run_value = _read_value(RUN_KEY)
    approved = _read_value(APPROVED_KEY)
    return windows_status(
        run_value if isinstance(run_value, str) else None,
        _expected_windows_command(),
        bytes(approved) if isinstance(approved, (bytes, bytearray)) else None,
    )


def _windows_set(enabled: bool) -> bool:
    reg = _winreg()
    if reg is None:
        return False
    try:
        if enabled:
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, _expected_windows_command())
        else:
            _delete_value(reg, RUN_KEY)
        # Turning it on in Madhura re-approves an entry the user had switched off in Windows; turning
        # it off removes our approval record too so nothing stale is left behind.
        _delete_value(reg, APPROVED_KEY)
        return True
    except OSError:
        log.exception("Could not update the Windows Run key")
        return False


def _delete_value(reg: Any, subkey: str) -> None:
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, subkey, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


# =========================================================================== Linux (XDG)


def desktop_file_path() -> Path:
    """``$XDG_CONFIG_HOME/autostart/madhura.desktop`` (``~/.config`` when unset or relative)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".config"
    return base / "autostart" / DESKTOP_FILE_NAME


_EXEC_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")


def _exec_arg(arg: str) -> str:
    if arg and not any(ch in _EXEC_RESERVED for ch in arg):
        quoted = arg
    else:
        escaped = "".join("\\" + ch if ch in '"`$\\' else ch for ch in arg)
        quoted = f'"{escaped}"'
    # Field codes use '%'; the value string itself escapes backslashes once more.
    return quoted.replace("%", "%%").replace("\\", "\\\\")


def desktop_exec(argv: Sequence[str]) -> str:
    """The ``Exec=`` value for *argv*, quoted per the Desktop Entry specification."""
    return " ".join(_exec_arg(str(a)) for a in argv)


def _escape_value(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def desktop_entry(argv: Sequence[str], *, workdir: str | None = None, icon: str | None = None) -> str:
    """Text of the XDG autostart entry that runs *argv*."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Plan the hour. Stay with it.",
        f"Exec={desktop_exec(argv)}",
    ]
    if workdir:
        lines.append(f"Path={_escape_value(workdir)}")
    if icon:
        lines.append(f"Icon={_escape_value(icon)}")
    lines += [
        "Terminal=false",
        "Categories=Utility;",
        "X-GNOME-Autostart-enabled=true",
    ]
    return "\n".join(lines) + "\n"


def _desktop_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_entry = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if in_entry and "=" in line:
            key, value = line.split("=", 1)
            fields.setdefault(key.strip(), value.strip())
    return fields


def desktop_status(text: str | None, expected_exec: str) -> str:
    """Status of an autostart entry's *text* (None = no file) against the ``Exec=`` we would write."""
    if text is None:
        return "off"
    fields = _desktop_fields(text)
    if fields.get("Hidden", "").lower() == "true":
        return "disabled_by_os"
    if fields.get("X-GNOME-Autostart-enabled", "true").lower() == "false":
        return "disabled_by_os"
    if fields.get("Exec") != expected_exec:
        return "needs_repair"
    return "on"


def _desktop_workdir() -> str | None:
    if _frozen():
        return None
    # Lets "-m madhura" work from a source checkout (harmless for an installed package).
    from madhura import paths

    return str(paths.repo_root())


def _desktop_icon() -> str | None:
    from madhura import paths

    icon = paths.asset("icon.png")
    return str(icon) if icon.is_file() else None


def _linux_status() -> str:
    path = desktop_file_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "off"
    except (OSError, UnicodeDecodeError):
        log.warning("Could not read %s", path, exc_info=True)
        return "needs_repair"
    return desktop_status(text, desktop_exec(launch_command()))


def _linux_set(enabled: bool) -> bool:
    path = desktop_file_path()
    try:
        if not enabled:
            path.unlink(missing_ok=True)
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            desktop_entry(launch_command(), workdir=_desktop_workdir(), icon=_desktop_icon()),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True
    except OSError:
        log.exception("Could not update %s", path)
        return False


# =========================================================================== public API


def is_supported() -> bool:
    """True when launch at login can be configured here (Windows and Linux; not macOS yet)."""
    return _IS_WINDOWS or _IS_LINUX


def status() -> str:
    """``"on"`` | ``"off"`` | ``"disabled_by_os"`` (switched off in the OS startup settings) |
    ``"needs_repair"`` (registered, but for another location/command). Never raises."""
    try:
        if _IS_WINDOWS:
            return _windows_status()
        if _IS_LINUX:
            return _linux_status()
    except Exception:  # noqa: BLE001 - OS integration must never break the app
        log.exception("Could not read the launch-at-login status")
    return "off"


def is_enabled() -> bool:
    """True when Madhura is registered to start at login and the OS has not switched it off
    (status ``"on"`` or ``"needs_repair"`` — the latter shows a Repair action)."""
    return status() in ("on", "needs_repair")


def set_enabled(enabled: bool) -> bool:
    """Register (or remove) launch at login. Enabling also repairs a stale entry and, on Windows,
    re-approves an entry switched off in the OS. Returns True on success; never raises."""
    try:
        if _IS_WINDOWS:
            return _windows_set(bool(enabled))
        if _IS_LINUX:
            return _linux_set(bool(enabled))
    except Exception:  # noqa: BLE001 - OS integration must never break the app
        log.exception("Could not change launch at login")
    return False
