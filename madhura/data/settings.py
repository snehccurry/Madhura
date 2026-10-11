"""User settings: dataclass + validators + debounced atomic JSON persistence (spec §6)."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from madhura.core.nudges import HHMM_RE

__all__ = [
    "SCHEMA_VERSION",
    "Settings",
    "SettingsStore",
    "VALIDATORS",
    "validate",
    "is_valid",
    "THEMES",
    "ACCENTS",
    "TIME_FORMATS",
    "AFTER_START",
    "NUDGE_MODES",
    "PILL_MODES",
    "CHECKIN_INTERVALS",
    "CHECKIN_TIMEOUTS",
]

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SAVE_DEBOUNCE_MS = 300

THEMES = ("system", "dark", "light")
ACCENTS = ("indigo", "teal", "rose", "amber", "violet")
TIME_FORMATS = ("system", "24h", "12h")
AFTER_START = ("mini", "stay")
NUDGE_MODES = ("off", "gentle", "firm")
PILL_MODES = ("hidden", "always", "never")
CHECKIN_INTERVALS = (5, 7, 10, 15, 20, 30)
CHECKIN_TIMEOUTS = (30, 60, 120)


@dataclass(frozen=True)
class Settings:
    """All persisted user settings with their defaults (immutable; use SettingsStore.update)."""

    theme: str = "dark"
    accent: str = "indigo"
    reduce_motion: bool = False
    time_format: str = "system"
    default_minutes: int = 60
    duration_presets: list[int] = field(default_factory=lambda: [15, 25, 45, 60, 90])
    daily_goal_minutes: int = 120
    day_start_hour: int = 4
    after_start: str = "mini"
    checkin_enabled: bool = True
    checkin_interval_minutes: int = 7
    checkin_adaptive: bool = True
    checkin_timeout_seconds: int = 60
    breaks_enabled: bool = True
    break_minutes: int = 5
    auto_start_break: bool = False
    nudge_mode: str = "gentle"
    nudge_idle_minutes: int = 5
    work_days: int = 31
    work_start: str = "09:00"
    work_end: str = "18:00"
    pill_mode: str = "hidden"
    pill_show_seconds: bool = False
    sound_volume: int = 40
    sound_checkin: bool = True
    sound_complete: bool = True
    sound_break_over: bool = True
    sound_habit: bool = True
    sound_nudge: bool = False
    close_to_tray: bool = True
    start_minimized: bool = False
    onboarded: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Plain dict of all fields (lists copied)."""
        return {f.name: _copy(getattr(self, f.name)) for f in fields(self)}


def _copy(value: Any) -> Any:
    return list(value) if isinstance(value, (list, tuple)) else value


# --------------------------------------------------------------------------- validators


def _is_bool(v: Any) -> bool:
    return type(v) is bool


def _is_int(v: Any) -> bool:
    return type(v) is int


def _choice(options: tuple[Any, ...]) -> Callable[[Any], bool]:
    def check(v: Any) -> bool:
        if isinstance(options[0], int):
            return _is_int(v) and v in options
        return isinstance(v, str) and v in options

    return check


def _int_range(lo: int, hi: int, multiple: int | None = None) -> Callable[[Any], bool]:
    def check(v: Any) -> bool:
        if not _is_int(v) or not lo <= v <= hi:
            return False
        return multiple is None or v % multiple == 0

    return check


def _hhmm(v: Any) -> bool:
    return isinstance(v, str) and bool(HHMM_RE.match(v))


def _presets(v: Any) -> bool:
    if not isinstance(v, (list, tuple)) or not 1 <= len(v) <= 6:
        return False
    if not all(_is_int(x) and 5 <= x <= 240 for x in v):
        return False
    values = list(v)
    return len(set(values)) == len(values) and values == sorted(values)


VALIDATORS: dict[str, Callable[[Any], bool]] = {
    "theme": _choice(THEMES),
    "accent": _choice(ACCENTS),
    "reduce_motion": _is_bool,
    "time_format": _choice(TIME_FORMATS),
    "default_minutes": _int_range(5, 240),
    "duration_presets": _presets,
    "daily_goal_minutes": _int_range(30, 720, multiple=30),
    "day_start_hour": _int_range(0, 6),
    "after_start": _choice(AFTER_START),
    "checkin_enabled": _is_bool,
    "checkin_interval_minutes": _choice(CHECKIN_INTERVALS),
    "checkin_adaptive": _is_bool,
    "checkin_timeout_seconds": _choice(CHECKIN_TIMEOUTS),
    "breaks_enabled": _is_bool,
    "break_minutes": _int_range(1, 30),
    "auto_start_break": _is_bool,
    "nudge_mode": _choice(NUDGE_MODES),
    "nudge_idle_minutes": _int_range(1, 60),
    "work_days": _int_range(0, 127),
    "work_start": _hhmm,
    "work_end": _hhmm,
    "pill_mode": _choice(PILL_MODES),
    "pill_show_seconds": _is_bool,
    "sound_volume": _int_range(0, 100),
    "sound_checkin": _is_bool,
    "sound_complete": _is_bool,
    "sound_break_over": _is_bool,
    "sound_habit": _is_bool,
    "sound_nudge": _is_bool,
    "close_to_tray": _is_bool,
    "start_minimized": _is_bool,
    "onboarded": _is_bool,
}

_FIELD_NAMES = tuple(f.name for f in fields(Settings))


def is_valid(key: str, value: Any) -> bool:
    """True when *value* is acceptable for setting *key* (KeyError for unknown keys)."""
    if key not in VALIDATORS:
        raise KeyError(key)
    return VALIDATORS[key](value)


def validate(key: str, value: Any) -> Any:
    """Return the normalized value or raise KeyError (unknown key) / ValueError (invalid)."""
    if not is_valid(key, value):
        raise ValueError(f"invalid value for setting {key!r}: {value!r}")
    return _copy(value)


def _settings_from_dict(data: dict[str, Any]) -> Settings:
    defaults = Settings()
    values: dict[str, Any] = {}
    for name in _FIELD_NAMES:
        if name not in data:
            continue
        value = data[name]
        if VALIDATORS[name](value):
            values[name] = _copy(value)
        else:
            log.warning("settings.json: invalid %s=%r; using default %r", name, value, getattr(defaults, name))
    unknown = set(data) - set(_FIELD_NAMES) - {"schema_version"}
    if unknown:
        log.info("settings.json: dropping unknown keys %s", ", ".join(sorted(unknown)))
    return dataclasses.replace(defaults, **values)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


class SettingsStore(QObject):
    """Owns the current :class:`Settings`, validates updates and persists them.

    ``update(**changes)`` is all-or-nothing (unknown key → KeyError, invalid → ValueError),
    emits ``changed(key)`` synchronously for each key whose value actually changed, and schedules
    a debounced (300 ms) atomic write; ``flush()`` writes immediately.
    """

    changed = Signal(str)

    def __init__(self, path: str | os.PathLike[str] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if path is None:
            from madhura import paths

            path = paths.settings_path()
        self._path = Path(path)
        self._dirty = False
        self._settings = self._load()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)

    # ------------------------------------------------------------------ access

    @property
    def path(self) -> Path:
        """settings.json location."""
        return self._path

    @property
    def settings(self) -> Settings:
        """Current immutable settings snapshot."""
        return self._settings

    @property
    def dirty(self) -> bool:
        """True while a write is pending."""
        return self._dirty

    def get(self, key: str) -> Any:
        """Value of one setting (KeyError for unknown keys; lists are copies)."""
        if key not in VALIDATORS:
            raise KeyError(key)
        return _copy(getattr(self._settings, key))

    def __getattr__(self, name: str) -> Any:
        # Convenience: store.daily_goal_minutes == store.settings.daily_goal_minutes.
        if name.startswith("_") or name not in VALIDATORS:
            raise AttributeError(name)
        return _copy(getattr(self._settings, name))

    # ------------------------------------------------------------------ mutation

    def update(self, **changes: Any) -> list[str]:
        """Validate and apply *changes*; returns the keys that actually changed."""
        normalized: dict[str, Any] = {}
        for key, value in changes.items():
            normalized[key] = validate(key, value)
        changed_keys = [k for k, v in normalized.items() if getattr(self._settings, k) != v]
        if not changed_keys:
            return []
        self._settings = dataclasses.replace(self._settings, **{k: normalized[k] for k in changed_keys})
        self._dirty = True
        self._schedule_save()
        for key in changed_keys:
            self.changed.emit(key)
        return changed_keys

    def reset_to_defaults(self, keep: tuple[str, ...] = ("onboarded",)) -> None:
        """Restore defaults (keeping *keep*), emitting ``changed`` for every key that changed."""
        defaults = Settings()
        changes = {
            f: getattr(defaults, f)
            for f in _FIELD_NAMES
            if f not in keep and getattr(self._settings, f) != getattr(defaults, f)
        }
        if changes:
            self.update(**changes)

    # ------------------------------------------------------------------ persistence

    def _schedule_save(self) -> None:
        if QCoreApplication.instance() is None:
            self.flush()
        else:
            self._timer.start()

    def flush(self) -> None:
        """Write pending changes to disk now (no-op when clean)."""
        self._timer.stop()
        if not self._dirty:
            return
        payload = {"schema_version": SCHEMA_VERSION, **self._settings.to_dict()}
        try:
            _atomic_write(self._path, json.dumps(payload, indent=2, sort_keys=False) + "\n")
            self._dirty = False
        except OSError:
            log.exception("Could not save settings to %s", self._path)

    def _load(self) -> Settings:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return Settings()
        except OSError:
            log.exception("Could not read %s; using defaults", self._path)
            return Settings()
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("settings.json must contain an object")
        except ValueError:
            bad = self._path.with_name(self._path.name + ".bad")
            log.warning("settings.json is corrupt; moving it to %s and using defaults", bad.name)
            try:
                os.replace(self._path, bad)
            except OSError:
                log.exception("Could not move corrupt settings file")
            return Settings()
        return _settings_from_dict(data)
