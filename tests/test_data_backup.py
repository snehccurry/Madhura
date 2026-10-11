from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from madhura.core.clock import FakeClock
from madhura.data import backup
from madhura.data.repository import Repository


def make_repo(path: Path, marker: str) -> Repository:
    repo = Repository(path)
    repo.kv_set("marker", marker)
    return repo


def test_backup_now(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "madhura.db", "live")
    try:
        dest = backup.backup_now(repo.conn, tmp_path / "bk", "pre-import")
        assert dest == tmp_path / "bk" / "madhura-pre-import.db"
        copy = sqlite3.connect(dest)
        assert copy.execute("SELECT value FROM kv WHERE key='marker'").fetchone()[0] == '"live"'
        copy.close()
        weird = backup.backup_now(repo.conn, tmp_path / "bk", "a/b c")
        assert weird.name == "madhura-a-b-c.db"
    finally:
        repo.close()


def test_backup_now_default_dir(data_dir: Path) -> None:
    repo = make_repo(data_dir / "madhura.db", "x")
    try:
        dest = backup.backup_now(repo.conn)
        assert dest.parent == data_dir / "backups"
        assert backup.list_backups()[0] == dest
    finally:
        repo.close()


def test_daily_backup_once_per_day_and_keeps_seven(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "madhura.db", "x")
    folder = tmp_path / "backups"
    try:
        start = date(2026, 10, 1)
        for offset in range(10):
            day = start + timedelta(days=offset)
            assert backup.daily_backup(repo.conn, today=day, dest_dir=folder) == folder / f"madhura-{day}.db"
        assert backup.daily_backup(repo.conn, today=start + timedelta(days=9), dest_dir=folder) is None
        (folder / "madhura-pre-import.db").write_bytes(b"keep me")
        backup.daily_backup(repo.conn, today=start + timedelta(days=10), dest_dir=folder)
        names = sorted(p.name for p in folder.iterdir())
        dailies = [n for n in names if n != "madhura-pre-import.db"]
        assert len(dailies) == 7
        assert dailies[0] == "madhura-2026-10-05.db"
        assert dailies[-1] == "madhura-2026-10-11.db"
        assert "madhura-pre-import.db" in names
    finally:
        repo.close()


def test_stage_and_apply_restore(data_dir: Path) -> None:
    db_path = data_dir / "madhura.db"
    old = make_repo(db_path, "old-state")
    snap = backup.backup_now(old.conn, tag="snapshot")
    old.kv_set("marker", "newer-state")
    old.close()

    staged = backup.stage_restore(snap)
    assert staged == data_dir / "madhura.db.restore"
    assert staged.exists()
    clock = FakeClock(datetime(2026, 10, 14, 9, 30))
    assert backup.apply_staged_restore(clock=clock) is True
    assert not staged.exists()
    restored = Repository(db_path)
    try:
        assert restored.kv_get("marker") == "old-state"
    finally:
        restored.close()
    kept = data_dir / "backups" / "madhura-pre-restore-20261014-093000.db"
    assert kept.exists()
    copy = sqlite3.connect(kept)
    assert copy.execute("SELECT value FROM kv WHERE key='marker'").fetchone()[0] == '"newer-state"'
    copy.close()
    assert backup.apply_staged_restore() is False  # nothing staged


def test_apply_restore_without_existing_db(tmp_path: Path) -> None:
    src = make_repo(tmp_path / "src.db", "src")
    snap = backup.backup_now(src.conn, tmp_path / "bk", "s")
    src.close()
    target = tmp_path / "fresh" / "madhura.db"
    target.parent.mkdir()
    backup.stage_restore(snap, db_path=target)
    assert backup.apply_staged_restore(target)
    repo = Repository(target)
    assert repo.kv_get("marker") == "src"
    repo.close()


def test_stage_restore_rejects_non_databases(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"nope" * 100)
    with pytest.raises(ValueError):
        backup.stage_restore(bogus, db_path=tmp_path / "madhura.db")
    with pytest.raises(ValueError):
        backup.stage_restore(tmp_path / "missing.db", db_path=tmp_path / "madhura.db")
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE foo(x)")
    conn.close()
    with pytest.raises(ValueError):
        backup.stage_restore(other, db_path=tmp_path / "madhura.db")
    assert not (tmp_path / "madhura.db.restore").exists()


def test_stage_restore_includes_uncheckpointed_wal(tmp_path: Path) -> None:
    """A source whose commits still sit in its -wal (copied from a running/crashed app, or a
    quarantined file) restores with those commits, as one standalone file. The folder name
    has URI-special characters, which must not break the read-only file: URI."""
    folder = tmp_path / "copy #2 ?100%"
    folder.mkdir()
    source = folder / "madhura.db"
    src = Repository(source)
    try:
        for i in range(5):
            src.add_habit(f"habit {i}")
        wal = source.with_name(source.name + "-wal")
        assert wal.exists() and wal.stat().st_size > 0  # still un-checkpointed
        live = tmp_path / "live" / "madhura.db"
        live.parent.mkdir()
        Repository(live).close()
        staged = backup.stage_restore(source, db_path=live)
        assert not staged.with_name(staged.name + "-wal").exists()
        assert wal.stat().st_size > 0  # the source was only read
    finally:
        src.close()
    assert backup.apply_staged_restore(live)
    restored = Repository(live)
    try:
        assert [h.name for h in restored.habits()] == [f"habit {i}" for i in range(5)]
    finally:
        restored.close()
