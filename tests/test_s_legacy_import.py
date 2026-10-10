"""[S] Legacy TinyDB habits.json import (spec §1.8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from madhura.data import legacy_import
from madhura.data.legacy_import import LegacyImportError, import_legacy_habits, normalize_time, read_legacy_habits

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_habits.json"
FIXTURE_NAMES = [
    "take meds",
    "call friend imp1",
    "meditate",
    "play basketball",
    "push a commit",
    "talk to dad",
    "plan the next day",
    "find opportunities",
]


def _write(path: Path, table: object) -> Path:
    path.write_text(json.dumps({"_default": table}), encoding="utf-8")
    return path


def test_fixture_imports_eight_habits_once(repo) -> None:
    assert import_legacy_habits(FIXTURE, repo) == 8
    habits = repo.habits()
    assert [h.name for h in habits] == FIXTURE_NAMES  # TinyDB doc-id order
    by_name = {h.name: h for h in habits}
    assert by_name["meditate"].time == "05:00"
    assert by_name["find opportunities"].time == "19:30"
    assert all(h.days == 127 and h.reminder and not h.archived for h in habits)
    # Idempotent: the second import adds nothing.
    assert import_legacy_habits(FIXTURE, repo) == 0
    assert len(repo.habits()) == 8


def test_duplicates_are_case_insensitive_and_include_archived(repo, tmp_path: Path) -> None:
    repo.add_habit("Meditate")
    archived = repo.add_habit("TAKE MEDS")
    repo.update_habit(archived, archived=True)
    assert import_legacy_habits(FIXTURE, repo) == 6
    names = [h.name for h in repo.habits(include_archived=True)]
    assert names.count("meditate") == 0 and "Meditate" in names


def test_repeats_inside_the_file_and_bad_rows_are_skipped(repo, tmp_path: Path) -> None:
    path = _write(
        tmp_path / "habits.json",
        {
            "10": {"title": "Read", "time": "7:30"},
            "2": {"title": "  Stretch   daily ", "time": "24:00"},
            "3": {"title": "read", "time": "08:00"},
            "4": {"name": "Journal", "time": None},
            "5": {"title": "", "time": "09:00"},
            "6": "not a dict",
            "7": {"title": 42},
        },
    )
    items = read_legacy_habits(path)
    assert [(h.name, h.time) for h in items] == [("Stretch daily", None), ("read", "08:00"), ("Journal", None)]
    assert import_legacy_habits(path, repo) == 3
    # "Read" (doc 10) comes after "read" (doc 3) in id order, so it's the in-file duplicate.
    assert sorted(h.name for h in repo.habits()) == ["Journal", "Stretch daily", "read"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("05:00", "05:00"),
        ("7:30", "07:30"),
        (" 09:15 ", "09:15"),
        ("23:59", "23:59"),
        ("24:00", None),
        ("12:60", None),
        ("noon", None),
        ("", None),
        (None, None),
        (5, None),
    ],
)
def test_normalize_time(raw: object, expected: str | None) -> None:
    assert normalize_time(raw) == expected


@pytest.mark.parametrize(
    "content",
    ["not json at all", json.dumps([1, 2]), json.dumps({"habits": {}}), json.dumps({"_default": []})],
)
def test_invalid_files_raise(content: str, tmp_path: Path, repo) -> None:
    path = tmp_path / "habits.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(LegacyImportError):
        import_legacy_habits(path, repo)
    assert repo.habits() == []


def test_missing_file_raises(tmp_path: Path, repo) -> None:
    with pytest.raises(LegacyImportError):
        import_legacy_habits(tmp_path / "nope.json", repo)


def test_empty_table_imports_nothing(tmp_path: Path, repo) -> None:
    assert import_legacy_habits(_write(tmp_path / "habits.json", {}), repo) == 0


def test_pending_counts_only_new_habits(repo) -> None:
    assert len(legacy_import.pending_legacy_habits(FIXTURE, repo)) == 8
    repo.add_habit("Talk to Dad")
    assert len(legacy_import.pending_legacy_habits(FIXTURE, repo)) == 7


def test_find_legacy_habits_uses_path_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(legacy_import.paths, "legacy_habits_candidates", lambda: [FIXTURE])
    assert legacy_import.find_legacy_habits() == [FIXTURE]

    def boom() -> list[Path]:
        raise OSError("disk gone")

    monkeypatch.setattr(legacy_import.paths, "legacy_habits_candidates", boom)
    assert legacy_import.find_legacy_habits() == []


def test_import_is_atomic(repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    original = repo.add_habit

    def flaky(name: str, **kw: object) -> int:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return original(name, **kw)

    monkeypatch.setattr(repo, "add_habit", flaky)
    with pytest.raises(RuntimeError):
        import_legacy_habits(FIXTURE, repo)
    assert repo.habits() == []
