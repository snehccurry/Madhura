"""Filesystem locations and environment switches.

Every directory helper creates its directory lazily on call (never at import).

Environment switches:
    MADHURA_DATA_DIR            override the data directory (tests, portable installs)
    MADHURA_NO_SINGLE_INSTANCE  "1" disables the single-instance lock/IPC
    MADHURA_NO_SOUND            "1" silences all sounds
    MADHURA_FORCE_TRAY          "0"/"1" overrides QSystemTrayIcon.isSystemTrayAvailable()
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "ENV_DATA_DIR",
    "ENV_NO_SINGLE_INSTANCE",
    "ENV_NO_SOUND",
    "ENV_FORCE_TRAY",
    "env_flag",
    "no_sound",
    "no_single_instance",
    "force_tray",
    "repo_root",
    "data_dir",
    "db_path",
    "settings_path",
    "log_dir",
    "cache_dir",
    "backups_dir",
    "asset",
    "legacy_habits_candidates",
]

log = logging.getLogger(__name__)

ENV_DATA_DIR = "MADHURA_DATA_DIR"
ENV_NO_SINGLE_INSTANCE = "MADHURA_NO_SINGLE_INSTANCE"
ENV_NO_SOUND = "MADHURA_NO_SOUND"
ENV_FORCE_TRAY = "MADHURA_FORCE_TRAY"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

LEGACY_HABITS_NAME = "habits.json"
_LEGACY_MAX_BYTES = 5 * 1024 * 1024
_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "site-packages", "AppData", "Library", "venv", ".venv"})


# --------------------------------------------------------------------------- env


def env_flag(name: str, default: bool = False) -> bool:
    """Return True when environment variable *name* is set to a truthy value ("1", "true", ...)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE or value == "":
        return False
    return default


def no_sound() -> bool:
    """True when MADHURA_NO_SOUND disables all audio."""
    return env_flag(ENV_NO_SOUND)


def no_single_instance() -> bool:
    """True when MADHURA_NO_SINGLE_INSTANCE disables the single-instance guard."""
    return env_flag(ENV_NO_SINGLE_INSTANCE)


def force_tray() -> bool | None:
    """MADHURA_FORCE_TRAY override: True/False when set to 1/0, else None (ask Qt)."""
    raw = os.environ.get(ENV_FORCE_TRAY)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


# --------------------------------------------------------------------------- dirs


def repo_root() -> Path:
    """Directory containing the ``madhura`` package (source checkout root)."""
    return Path(__file__).resolve().parent.parent


def _ensure(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("Could not create directory %s", path)
    return path


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "Madhura"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Madhura"
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".local" / "share"
    return root / "madhura"


def data_dir() -> Path:
    """Per-user data directory (MADHURA_DATA_DIR first), created if missing."""
    override = os.environ.get(ENV_DATA_DIR)
    path = Path(override).expanduser() if override else _default_data_dir()
    return _ensure(path)


def db_path() -> Path:
    """Path of the SQLite database file."""
    return data_dir() / "madhura.db"


def settings_path() -> Path:
    """Path of settings.json."""
    return data_dir() / "settings.json"


def log_dir() -> Path:
    """Directory for madhura.log / crash.log, created if missing."""
    return _ensure(data_dir() / "logs")


def cache_dir() -> Path:
    """Directory for regenerable caches (e.g. volume-scaled sounds), created if missing."""
    return _ensure(data_dir() / "cache")


def backups_dir() -> Path:
    """Directory for daily / pre-import / pre-restore database backups, created if missing."""
    return _ensure(data_dir() / "backups")


def asset(rel: str | os.PathLike[str]) -> Path:
    """Absolute path of a bundled asset (PyInstaller ``sys._MEIPASS`` aware).

    ``asset("sounds/chime.wav")`` → ``<base>/madhura/assets/sounds/chime.wav``.
    """
    base = Path(getattr(sys, "_MEIPASS", repo_root()))
    return base / "madhura" / "assets" / Path(rel)


# --------------------------------------------------------------------------- legacy


def _looks_like_legacy_habits(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size > _LEGACY_MAX_BYTES:
            return False
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return '"_default"' in fh.read()
    except OSError:
        return False


def _scan(root: Path, depth: int, found: list[Path], seen: set[Path], budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    budget[0] -= 1
    candidate = root / LEGACY_HABITS_NAME
    if _looks_like_legacy_habits(candidate):
        key = candidate.resolve()
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    if depth <= 0:
        return
    try:
        with os.scandir(root) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith((".", "$")) or entry.name in _SKIP_DIRS:
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                _scan(Path(entry.path), depth - 1, found, seen, budget)
        except OSError:
            continue


def legacy_habits_candidates(
    roots: Iterable[Path] | None = None, depth: int = 2, max_dirs_per_root: int = 400
) -> list[Path]:
    """Find old TinyDB ``habits.json`` files (containing ``"_default"``).

    Default roots: cwd, dirname(sys.executable), ~/Desktop, ~/Downloads. Each root is scanned
    to *depth* directory levels below it (root itself, children, grandchildren), visiting at most
    *max_dirs_per_root* directories (hidden, ``$``-prefixed and dependency folders skipped) so a
    huge cwd such as System32 stays fast. Returns existing, de-duplicated files in discovery
    order. Never raises.
    """
    if roots is None:
        home = Path.home()
        roots = [
            Path.cwd(),
            Path(sys.executable).resolve().parent,
            home / "Desktop",
            home / "Downloads",
        ]
    found: list[Path] = []
    seen: set[Path] = set()
    visited: set[Path] = set()
    for root in roots:
        try:
            resolved = Path(root).expanduser().resolve()
        except OSError:
            continue
        if resolved in visited or not resolved.is_dir():
            continue
        visited.add(resolved)
        _scan(resolved, depth, found, seen, [max_dirs_per_root])
    return found
