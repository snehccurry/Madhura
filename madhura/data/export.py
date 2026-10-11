"""CSV / JSON exports (spec §1.9 Insights "Export sessions (CSV)…", §1.10 "Export everything (JSON)…").

Both writers are atomic: they write a temporary file next to the target and ``os.replace`` it, so a
failed export never leaves a half-written file behind. Errors (``OSError``) propagate to the caller,
which shows them via ``ui.dialogs.show_error``.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from madhura import APP_NAME, __version__
from madhura.core.days import logical_date
from madhura.core.models import to_date_str, to_iso

__all__ = ["CSV_COLUMNS", "export_all_json", "export_sessions_csv", "sessions_csv_rows"]

#: Column order of the sessions CSV.
CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "date",
    "started_at",
    "ended_at",
    "task",
    "project",
    "outcome",
    "planned_minutes",
    "focused_minutes",
    "paused_minutes",
    "checkins_focused",
    "checkins_drifted",
    "checkins_missed",
    "focus_score",
    "note",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_ALL_TIME_END = datetime(9999, 1, 1)


def _safe_text(value: str | None) -> str:
    """Free text for a spreadsheet cell; text that a spreadsheet would run as a formula gets a
    leading apostrophe (CSV injection guard)."""
    text = value or ""
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _minutes(seconds: float) -> str:
    """Seconds → minutes with two decimals (``"42.5"``, ``"60"``)."""
    value = round(max(0.0, float(seconds)) / 60.0, 2)
    return f"{value:.2f}".rstrip("0").rstrip(".") if value else "0"


def _atomic_write(path: str | os.PathLike[str], write: Any, *, encoding: str, newline: str | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sessions_csv_rows(
    repo: Any,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    day_start_hour: int = 4,
) -> list[list[Any]]:
    """The CSV data rows (without the header) for finished sessions with ``start <= started_at < end``,
    oldest first. Abandoned sessions are included (outcome column); active ones never are."""
    sessions = repo.recent_sessions(limit=10_000_000, start_dt=start, end_dt=end, include_abandoned=True)
    sessions.reverse()
    by_session: dict[int, dict[str, int]] = {}
    if sessions:
        first = min(s.started_at for s in sessions)
        ids = {s.id for s in sessions}
        for c in _checkins_since(repo, first):
            if c.session_id in ids:
                counts = by_session.setdefault(c.session_id, {"focused": 0, "drifted": 0, "missed": 0})
                key = str(getattr(c.response, "value", c.response))
                if key in counts:
                    counts[key] += 1
    rows: list[list[Any]] = []
    for s in sessions:
        counts = by_session.get(s.id, {"focused": 0, "drifted": 0, "missed": 0})
        answered = counts["focused"] + counts["drifted"]
        score = f"{round(100 * counts['focused'] / answered)}" if answered else ""
        rows.append(
            [
                s.id,
                to_date_str(logical_date(s.started_at, day_start_hour)),
                to_iso(s.started_at),
                to_iso(s.ended_at) if s.ended_at else "",
                _safe_text(s.task),
                _safe_text(s.project),
                str(getattr(s.outcome, "value", s.outcome)),
                round(s.planned_seconds / 60),
                _minutes(s.focused_seconds),
                _minutes(s.paused_seconds),
                counts["focused"],
                counts["drifted"],
                counts["missed"],
                score,
                _safe_text(s.note),
            ]
        )
    return rows


def _checkins_since(repo: Any, first: datetime) -> Iterable[Any]:
    return repo.checkins_between(first, _ALL_TIME_END)


def export_sessions_csv(
    path: str | os.PathLike[str],
    repo: Any,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    day_start_hour: int = 4,
) -> int:
    """Write finished sessions (optionally ``start <= started_at < end``) to *path* as CSV.

    UTF-8 with a byte-order mark (so spreadsheet apps detect the encoding), one row per session,
    oldest first, columns :data:`CSV_COLUMNS`; ``date`` is the logical date. Returns the row count.
    """
    rows = sessions_csv_rows(repo, start, end, day_start_hour=day_start_hour)

    def write(fh: Any) -> None:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)

    _atomic_write(path, write, encoding="utf-8-sig", newline="")
    return len(rows)


def export_all_json(path: str | os.PathLike[str], repo: Any, *, exported_at: datetime | None = None) -> dict[str, int]:
    """Write everything (``repo.export_dict()``) to *path* as pretty UTF-8 JSON.

    The document carries the metadata ``app``, ``version`` and ``exported_at`` (``exported_at``
    defaults to the repository clock's now). Returns ``{table: row count}``.
    """
    data = dict(repo.export_dict())
    data["app"] = APP_NAME
    data["version"] = __version__
    if exported_at is not None:
        data["exported_at"] = to_iso(exported_at)
    elif "exported_at" not in data:
        data["exported_at"] = to_iso(repo.clock.now())
    ordered: dict[str, Any] = {"app": data.pop("app"), "version": data.pop("version"), "exported_at": data.pop("exported_at")}
    ordered.update(data)

    def write(fh: Any) -> None:
        json.dump(ordered, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")

    _atomic_write(path, write, encoding="utf-8")
    return {key: len(value) for key, value in ordered.items() if isinstance(value, list)}

