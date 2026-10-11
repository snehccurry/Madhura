from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from madhura.core.clock import FakeClock
from madhura.data import db as dbmod
from madhura.data.repository import Repository

TABLES = {"sessions", "checkins", "tasks", "habits", "habit_logs", "captures", "day_notes", "kv"}


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_connect_pragmas_and_schema(tmp_path: Path) -> None:
    conn, recovered = dbmod.open_database(tmp_path / "m.db")
    try:
        assert recovered is None
        assert conn.isolation_level is None
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 3000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert dbmod.user_version(conn) == dbmod.SCHEMA_VERSION
        assert TABLES <= table_names(conn)
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for name in (
            "idx_sessions_started_at",
            "idx_checkins_session",
            "idx_checkins_at",
            "idx_habit_logs_date",
            "idx_tasks_plan_date",
        ):
            assert name in indexes
        assert dbmod.quick_check(conn)
    finally:
        dbmod.checkpoint_and_close(conn)


def test_wal_fallback_to_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    # In-memory databases cannot use WAL ("memory" is returned) → we fall back to DELETE silently.
    conn = dbmod.connect(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] in ("memory", "delete")
    finally:
        conn.close()


def test_migrate_is_idempotent_and_returns_old_version(tmp_path: Path) -> None:
    conn = dbmod.connect(tmp_path / "x.db")
    assert dbmod.migrate(conn, tmp_path / "x.db") == 0
    assert dbmod.migrate(conn, tmp_path / "x.db") == dbmod.SCHEMA_VERSION
    conn.close()


def test_pre_migration_backup_keeps_last_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "madhura.db"
    conn = dbmod.connect(path)
    dbmod.migrate(conn, path)
    conn.execute("INSERT INTO kv(key, value) VALUES ('k', '1')")
    # Pretend future versions 2..4 exist and migrate step by step.
    for version in (2, 3, 4):
        monkeypatch.setitem(dbmod.MIGRATIONS, version, [f"CREATE TABLE t{version}(x)"])
        monkeypatch.setattr(dbmod, "SCHEMA_VERSION", version)
        assert dbmod.migrate(conn, path) == version - 1
        assert (path.parent / f"madhura.db.bak-v{version - 1}").exists()
    backups = sorted(p.name for p in tmp_path.glob("madhura.db.bak-v*"))
    assert backups == ["madhura.db.bak-v2", "madhura.db.bak-v3"]
    copy = sqlite3.connect(tmp_path / "madhura.db.bak-v3")
    assert copy.execute("SELECT value FROM kv WHERE key='k'").fetchone()[0] == "1"
    assert dbmod.user_version(copy) == 3
    copy.close()
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "madhura.db"
    conn = dbmod.connect(path)
    dbmod.migrate(conn, path)
    monkeypatch.setitem(dbmod.MIGRATIONS, 2, ["CREATE TABLE ok(x)", "THIS IS NOT SQL"])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 2)
    with pytest.raises(sqlite3.Error):
        dbmod.migrate(conn, path)
    assert dbmod.user_version(conn) == 1
    assert "ok" not in table_names(conn)
    conn.close()


def test_corrupt_database_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "madhura.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 100)
    clock = FakeClock(datetime(2026, 10, 14, 10, 0, 5))
    repo = Repository(path, clock=clock)
    try:
        assert repo.recovered_from is not None
        assert repo.recovered_from.name == "madhura.corrupt-20261014-100005.db"
        assert repo.recovered_from.exists()
        assert repo.kv_get("recovered_from") == "madhura.corrupt-20261014-100005.db"
        repo.add_task("works", plan_date=datetime(2026, 10, 14).date())
        assert len(repo.tasks_for_date(datetime(2026, 10, 14).date())) == 1
    finally:
        repo.close()


def test_quarantine_name_collision(tmp_path: Path) -> None:
    path = tmp_path / "madhura.db"
    clock = FakeClock(datetime(2026, 10, 14, 10, 0))
    path.write_bytes(b"a")
    first = dbmod.quarantine(path, clock)
    path.write_bytes(b"b")
    (tmp_path / "madhura.db-wal").write_bytes(b"w")
    second = dbmod.quarantine(path, clock)
    assert first != second
    assert first.exists() and second.exists()
    assert not path.exists()
    assert (tmp_path / (second.name + "-wal")).exists()


def test_quick_check_failure_triggers_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "madhura.db"
    Repository(path).close()
    monkeypatch.setattr(dbmod, "quick_check", lambda conn: False)
    seen: list[Path] = []
    conn, moved = dbmod.open_database(path, FakeClock(datetime(2026, 1, 1)), on_recovered=seen.append)
    try:
        assert moved is not None and seen == [moved]
        assert path.exists()
    finally:
        conn.close()


def test_memory_database() -> None:
    conn, moved = dbmod.open_database(":memory:")
    assert moved is None
    assert TABLES <= table_names(conn)
    conn.close()


def test_checkpoint_and_close_truncates_wal(tmp_path: Path) -> None:
    path = tmp_path / "madhura.db"
    repo = Repository(path)
    repo.kv_set("x", list(range(1000)))
    repo.close()
    repo.close()  # idempotent
    wal = tmp_path / "madhura.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0
    assert repo.closed


# --------------------------------------------------------------------------- locked ≠ corrupt


def _no_corrupt_files(folder: Path) -> bool:
    return not [p for p in folder.iterdir() if ".corrupt-" in p.name]


def test_locked_database_is_not_quarantined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another program holding an exclusive lock must not make a healthy DB look damaged."""
    path = tmp_path / "madhura.db"
    repo = Repository(path)
    repo.add_habit("keep me")
    repo.close()
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("PRAGMA locking_mode=EXCLUSIVE")
    blocker.execute("BEGIN EXCLUSIVE")
    real_connect = sqlite3.connect
    monkeypatch.setattr(dbmod.sqlite3, "connect", lambda *a, **kw: real_connect(*a, **{**kw, "timeout": 0.05}))
    sleeps: list[float] = []
    monkeypatch.setattr(dbmod.time, "sleep", sleeps.append)
    try:
        with pytest.raises(dbmod.DatabaseUnavailable) as info:
            Repository(path)
        assert info.value.path == path and dbmod.is_unavailable_error(info.value.reason)
        assert sleeps == []  # SQLite's busy handler already waited; no extra retries
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert _no_corrupt_files(tmp_path)
    repo = Repository(path)
    try:
        assert repo.recovered_from is None
        assert [h.name for h in repo.habits()] == ["keep me"]
    finally:
        repo.close()


def test_transient_open_errors_are_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "madhura.db"
    Repository(path).close()
    real = dbmod._open_checked
    calls: list[Path] = []
    fail_times = [2]

    def flaky(p: Path) -> sqlite3.Connection:
        calls.append(p)
        if len(calls) <= fail_times[0]:
            # Built by hand → no sqlite_errorcode, like Python 3.10: matched by message.
            raise sqlite3.OperationalError("disk I/O error")
        return real(p)

    sleeps: list[float] = []
    monkeypatch.setattr(dbmod, "_open_checked", flaky)
    monkeypatch.setattr(dbmod.time, "sleep", sleeps.append)
    conn, moved = dbmod.open_database(path)
    conn.close()
    assert moved is None and len(calls) == 3 and sleeps == list(dbmod.OPEN_RETRY_DELAYS_S)

    # Still failing after the retries → DatabaseUnavailable, nothing renamed or created.
    calls.clear()
    fail_times[0] = 99
    with pytest.raises(dbmod.DatabaseUnavailable):
        dbmod.open_database(path)
    assert len(calls) == len(dbmod.OPEN_RETRY_DELAYS_S) + 1
    assert path.exists() and _no_corrupt_files(tmp_path)


def test_quarantine_failure_is_unavailable_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: the damaged file can't be renamed while another process has it open."""
    path = tmp_path / "madhura.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 100)

    def sharing_violation(src: object, dst: object) -> None:
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(dbmod.os, "replace", sharing_violation)
    with pytest.raises(dbmod.DatabaseUnavailable) as info:
        dbmod.open_database(path)
    assert isinstance(info.value.reason, PermissionError)
    assert path.read_bytes().startswith(b"this is definitely")


def test_foreign_schema_is_still_quarantined(tmp_path: Path) -> None:
    """SQLITE_ERROR (e.g. a migration hitting someone else's 'sessions' table) is not 'unavailable'."""
    path = tmp_path / "madhura.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions(x)")
    conn.close()
    conn, moved = dbmod.open_database(path, FakeClock(datetime(2026, 1, 1)))
    try:
        assert moved is not None and moved.exists()
        assert TABLES <= table_names(conn)
    finally:
        conn.close()


def test_quick_check_raises_when_locked_but_not_when_damaged() -> None:
    class Conn:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def execute(self, _sql: str) -> object:
            raise self.exc

    assert dbmod.quick_check(Conn(sqlite3.DatabaseError("file is not a database"))) is False  # type: ignore[arg-type]
    with pytest.raises(sqlite3.OperationalError):
        dbmod.quick_check(Conn(sqlite3.OperationalError("database is locked")))  # type: ignore[arg-type]
    assert not dbmod.is_unavailable_error(sqlite3.OperationalError("no such column: started_at"))
    assert not dbmod.is_unavailable_error(sqlite3.DatabaseError("database disk image is malformed"))
