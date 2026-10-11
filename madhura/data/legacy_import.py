"""Import habits from the original Madhura's TinyDB ``habits.json`` (spec §1.8).

The old app stored ``{"_default": {"1": {"title": "meditate", "time": "05:00", ...}, ...}}``.
Each document becomes a habit: ``title`` → name, ``time`` kept when it is a valid ``HH:MM``
(``H:MM`` is zero-padded), every day of the week, reminders on. Names that already exist
(case-insensitive, archived habits included) and repeats inside the file are skipped, so importing
twice adds nothing the second time. Callers take a backup first (``data.backup.backup_now``) and
emit ``data_changed('habits')``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from madhura import paths

__all__ = [
    "LegacyHabit",
    "LegacyImportError",
    "find_legacy_habits",
    "read_legacy_habits",
    "pending_legacy_habits",
    "import_legacy_habits",
    "normalize_time",
]

log = logging.getLogger(__name__)

_MAX_BYTES = 5 * 1024 * 1024
_MAX_NAME = 120
_TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


class LegacyImportError(ValueError):
    """The file is missing, unreadable or not an old Madhura ``habits.json``."""


@dataclass(frozen=True)
class LegacyHabit:
    """One habit read from a legacy file."""

    name: str
    time: str | None


def normalize_time(value: Any) -> str | None:
    """``"HH:MM"`` for a valid 24-hour time (``"7:30"`` → ``"07:30"``), else None (anytime)."""
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value)
    if match is None:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _doc_order(key: str) -> tuple[int, int | str]:
    try:
        return (0, int(key))
    except (TypeError, ValueError):
        return (1, str(key))


def find_legacy_habits() -> list[Path]:
    """Old ``habits.json`` files near the app: cwd, the executable's folder, ~/Desktop and
    ~/Downloads (two levels deep). Never raises."""
    try:
        return paths.legacy_habits_candidates()
    except Exception:  # noqa: BLE001 - detection is best effort
        log.exception("Looking for old Madhura habits failed")
        return []


def read_legacy_habits(path: str | os.PathLike[str]) -> list[LegacyHabit]:
    """Parse a legacy file into habits in their original order (repeats inside the file removed).

    Raises :class:`LegacyImportError` when the file can't be read or isn't a TinyDB habits file.
    """
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_BYTES:
            raise LegacyImportError(f"{source.name} is too large to be a Madhura habits file")
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except LegacyImportError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise LegacyImportError(f"Could not read {source.name}") from exc
    table = data.get("_default") if isinstance(data, dict) else None
    if not isinstance(table, dict):
        raise LegacyImportError(f"{source.name} is not an old Madhura habits file")
    out: list[LegacyHabit] = []
    seen: set[str] = set()
    for key in sorted(table, key=_doc_order):
        row = table[key]
        if not isinstance(row, dict):
            continue
        raw = row.get("title")
        if not isinstance(raw, str):
            raw = row.get("name")
        name = " ".join(raw.split())[:_MAX_NAME].strip() if isinstance(raw, str) else ""
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(LegacyHabit(name, normalize_time(row.get("time"))))
    return out


def pending_legacy_habits(path: str | os.PathLike[str], repo: Any) -> list[LegacyHabit]:
    """The habits an import of *path* would add now (existing names skipped).

    Raises :class:`LegacyImportError` like :func:`read_legacy_habits`.
    """
    existing = {h.name.strip().casefold() for h in repo.habits(include_archived=True)}
    return [h for h in read_legacy_habits(path) if h.name.casefold() not in existing]


def import_legacy_habits(path: str | os.PathLike[str], repo: Any) -> int:
    """Add the habits from a legacy file in one transaction; returns how many were added.

    Duplicate names (case-insensitive, against existing habits and within the file) are skipped,
    so a second import returns 0. Raises :class:`LegacyImportError` for an unreadable/invalid file.
    """
    new = pending_legacy_habits(path, repo)
    if not new:
        return 0
    with repo.transaction():
        for habit in new:
            repo.add_habit(habit.name, time=habit.time)
    log.info("Imported %d habits from %s", len(new), path)
    return len(new)
