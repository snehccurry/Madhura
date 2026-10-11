"""data.export — sessions CSV and everything-JSON [I]."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from madhura import __version__
from madhura.data import export
from madhura.data.repository import Repository


def _session(repo: Repository, start: datetime, focused_min: int, outcome: str = "completed", **fields) -> int:  # noqa: ANN003
    sid = repo.create_session(fields.pop("task", "Write the report"), fields.pop("project", None), None, start, focused_min * 60)
    repo.update_session(
        sid,
        focused_seconds=focused_min * 60,
        paused_seconds=fields.pop("paused", 0),
        ended_at=start + timedelta(minutes=focused_min),
        outcome=outcome,
        **fields,
    )
    return sid


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_csv_columns_rows_and_order(repo: Repository, tmp_path: Path) -> None:
    a = _session(repo, datetime(2026, 10, 14, 9, 0), 45, project="Madhura", note="Good flow")
    b = _session(repo, datetime(2026, 10, 13, 15, 0), 30, "ended_early", task="Read", paused=90)
    c = _session(repo, datetime(2026, 10, 15, 1, 30), 5, "abandoned", task="Late")
    repo.create_session("Running", None, None, datetime(2026, 10, 14, 11, 0), 1500)  # active → never exported
    for resp, reason in (("focused", None), ("focused", None), ("drifted", "Phone"), ("missed", None)):
        repo.add_checkin(a, datetime(2026, 10, 14, 9, 10), resp, reason)
    path = tmp_path / "out" / "sessions.csv"
    n = export.export_sessions_csv(path, repo)
    assert n == 3
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for spreadsheet apps
    rows = _read(path)
    assert list(rows[0].keys()) == list(export.CSV_COLUMNS)
    assert [int(r["id"]) for r in rows] == [b, a, c]  # oldest first
    ra = rows[1]
    assert ra["task"] == "Write the report" and ra["project"] == "Madhura" and ra["note"] == "Good flow"
    assert ra["started_at"] == "2026-10-14T09:00:00" and ra["ended_at"] == "2026-10-14T09:45:00"
    assert ra["outcome"] == "completed" and ra["planned_minutes"] == "45" and ra["focused_minutes"] == "45"
    assert (ra["checkins_focused"], ra["checkins_drifted"], ra["checkins_missed"]) == ("2", "1", "1")
    assert ra["focus_score"] == "67"
    rb = rows[0]
    assert rb["paused_minutes"] == "1.5" and rb["focus_score"] == "" and rb["outcome"] == "ended_early"
    rc = rows[2]
    assert rc["outcome"] == "abandoned"
    assert rc["date"] == "2026-10-14"  # 01:30 belongs to the previous logical day (day start 4)


def test_csv_range_and_day_start(repo: Repository, tmp_path: Path) -> None:
    _session(repo, datetime(2026, 10, 10, 9), 25)
    keep = _session(repo, datetime(2026, 10, 14, 9), 25)
    _session(repo, datetime(2026, 10, 15, 9), 25)
    path = tmp_path / "range.csv"
    n = export.export_sessions_csv(path, repo, datetime(2026, 10, 11, 4), datetime(2026, 10, 15, 4), day_start_hour=0)
    rows = _read(path)
    assert n == 1 and [int(r["id"]) for r in rows] == [keep]
    assert rows[0]["date"] == "2026-10-14"


def test_csv_empty_still_has_header(repo: Repository, tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    assert export.export_sessions_csv(path, repo) == 0
    with open(path, encoding="utf-8-sig") as fh:
        assert fh.read().strip() == ",".join(export.CSV_COLUMNS)


def test_csv_guards_formula_cells_and_quotes(repo: Repository, tmp_path: Path) -> None:
    _session(repo, datetime(2026, 10, 14, 9), 25, task='=HYPERLINK("x")', project="+cmd", note='said "hi", then left\nnext line')
    path = tmp_path / "inj.csv"
    export.export_sessions_csv(path, repo)
    row = _read(path)[0]
    assert row["task"] == "'=HYPERLINK(\"x\")"
    assert row["project"] == "'+cmd"
    assert row["note"] == 'said "hi", then left\nnext line'


def test_csv_failure_leaves_no_partial_file(repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _session(repo, datetime(2026, 10, 14, 9), 25)
    out = tmp_path / "exports"
    out.mkdir()
    path = out / "fail.csv"
    path.write_text("old", encoding="utf-8")

    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("disk full")

    monkeypatch.setattr(export.os, "replace", boom)
    with pytest.raises(OSError):
        export.export_sessions_csv(path, repo)
    assert path.read_text(encoding="utf-8") == "old"
    assert [p.name for p in out.iterdir()] == ["fail.csv"]


def test_json_has_everything_and_metadata(repo: Repository, tmp_path: Path) -> None:
    sid = _session(repo, datetime(2026, 10, 14, 9), 25, project="Madhura")
    repo.add_checkin(sid, datetime(2026, 10, 14, 9, 7), "drifted", "Phone")
    repo.add_task("Plan", 25, plan_date=datetime(2026, 10, 14).date())
    hid = repo.add_habit("Read", "📖")
    repo.set_habit_status(hid, datetime(2026, 10, 14).date(), "done")
    repo.add_capture("Idea ✨")
    repo.set_day_note(datetime(2026, 10, 14).date(), "Good day")
    repo.kv_set("weekly_recap_seen", "2026-W42")
    path = tmp_path / "all.json"
    counts = export.export_all_json(path, repo, exported_at=datetime(2026, 10, 14, 18, 0))
    text = path.read_text(encoding="utf-8")
    assert "Idea ✨" in text and "📖" in text  # not ASCII-escaped
    data = json.loads(text)
    assert list(data)[:3] == ["app", "version", "exported_at"]
    assert data["app"] == "Madhura" and data["version"] == __version__
    assert data["exported_at"] == "2026-10-14T18:00:00"
    for table in ("sessions", "checkins", "tasks", "habits", "habit_logs", "captures", "day_notes"):
        assert len(data[table]) == 1, table
        assert counts[table] == 1
    assert data["checkins"][0]["reason"] == "Phone"
    assert data["kv"]["weekly_recap_seen"] == "2026-W42"
    assert "schema_version" in data


def test_json_default_exported_at_uses_repo_clock(repo: Repository, tmp_path: Path, fake_clock) -> None:  # noqa: ANN001
    path = tmp_path / "all.json"
    export.export_all_json(path, repo)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["exported_at"] == fake_clock.now().replace(microsecond=0).isoformat()
