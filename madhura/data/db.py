"""SQLite connection, schema migrations and corruption recovery (spec §4)."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from madhura.core.clock import Clock, SystemClock

__all__ = [
    "SCHEMA_VERSION",
    "MIGRATIONS",
    "connect",
    "migrate",
    "user_version",
    "quick_check",
    "open_database",
    "DatabaseUnavailable",
    "is_unavailable_error",
    "quarantine",
    "checkpoint_and_close",
    "backup_to",
    "sidecar_paths",
]

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_KEEP_MIGRATION_BACKUPS = 2

#: Pauses between attempts when the file can't be opened right now (I/O error, drive not ready).
#: A locked file is not retried: SQLite's busy handler has already waited.
OPEN_RETRY_DELAYS_S: tuple[float, ...] = (0.25, 0.75)

# Primary result codes that mean "can't get at the file right now", not "the file is damaged":
# PERM, BUSY, LOCKED, READONLY, IOERR, FULL, CANTOPEN, PROTOCOL. Corruption is SQLITE_CORRUPT /
# SQLITE_NOTADB, which Python raises as plain DatabaseError, never OperationalError.
_UNAVAILABLE_CODES = frozenset({3, 5, 6, 8, 10, 13, 14, 15})
# Python 3.10 has no ``sqlite_errorcode``: match SQLite's messages for the same codes.
_UNAVAILABLE_MESSAGES = (
    "access permission denied",
    "database is locked",
    "database table is locked",
    "readonly database",
    "disk i/o error",
    "database or disk is full",
    "unable to open database",
    "locking protocol",
)


class DatabaseUnavailable(Exception):
    """The database exists but can't be opened right now (locked by another program, I/O error,
    read-only, drive not reachable). Nothing was renamed or created."""

    def __init__(self, path: Path, reason: BaseException) -> None:
        super().__init__(f"{path} can't be opened right now: {reason}")
        self.path = path
        self.reason = reason


def is_unavailable_error(exc: BaseException) -> bool:
    """True for SQLite errors meaning the file is unreachable/locked rather than corrupt."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in _UNAVAILABLE_CODES
    text = str(exc).lower()
    return any(m in text for m in _UNAVAILABLE_MESSAGES)


def _is_busy(exc: BaseException) -> bool:
    """SQLITE_BUSY / SQLITE_LOCKED: SQLite's busy handler has already waited before raising."""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in (5, 6)
    return "is locked" in str(exc).lower()

_V1 = [
    """
    CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY,
        task TEXT NOT NULL,
        project TEXT,
        planned_task_id INTEGER,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        planned_seconds INTEGER NOT NULL,
        focused_seconds INTEGER NOT NULL DEFAULT 0,
        paused_seconds INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL DEFAULT 'active',
        note TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkins(
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        response TEXT NOT NULL CHECK(response IN ('focused','drifted','missed')),
        reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        project TEXT,
        minutes INTEGER NOT NULL DEFAULT 25,
        plan_date TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS habits(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        emoji TEXT,
        cue TEXT,
        time TEXT,
        days INTEGER NOT NULL DEFAULT 127,
        reminder INTEGER NOT NULL DEFAULT 1,
        urgent INTEGER NOT NULL DEFAULT 0,
        focus_minutes INTEGER,
        project TEXT,
        position INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS habit_logs(
        id INTEGER PRIMARY KEY,
        habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('done','skipped')),
        at TEXT NOT NULL,
        UNIQUE(habit_id, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS captures(
        id INTEGER PRIMARY KEY,
        session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
        text TEXT NOT NULL,
        at TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE TABLE IF NOT EXISTS day_notes(date TEXT PRIMARY KEY, note TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_checkins_session ON checkins(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_checkins_at ON checkins(at)",
    "CREATE INDEX IF NOT EXISTS idx_habit_logs_date ON habit_logs(date)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_plan_date ON tasks(plan_date)",
]

#: version → statements upgrading from version-1 to version.
MIGRATIONS: dict[int, list[str]] = {1: _V1}


def sidecar_paths(db_file: Path) -> list[Path]:
    """The database file plus its ``-wal`` / ``-shm`` / ``-journal`` siblings."""
    return [db_file] + [db_file.with_name(db_file.name + suffix) for suffix in ("-wal", "-shm", "-journal")]


def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open an autocommit connection with WAL (fallback DELETE), NORMAL sync, FKs and busy timeout."""
    conn = sqlite3.connect(os.fspath(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if not mode or str(mode[0]).lower() != "wal":
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=3000")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def user_version(conn: sqlite3.Connection) -> int:
    """``PRAGMA user_version``."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def quick_check(conn: sqlite3.Connection) -> bool:
    """True when ``PRAGMA quick_check`` reports 'ok'.

    A locked or unreachable file raises (see :func:`is_unavailable_error`) instead of reading as
    damaged.
    """
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError as exc:
        if is_unavailable_error(exc):
            raise
        return False
    return len(rows) == 1 and str(rows[0][0]).lower() == "ok"


def backup_to(conn: sqlite3.Connection, dest: Path) -> Path:
    """Consistent online copy of *conn* to *dest* via ``Connection.backup`` (atomic replace)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    target = sqlite3.connect(os.fspath(tmp))
    try:
        conn.backup(target)
    finally:
        target.close()
    os.replace(tmp, dest)
    return dest


def _prune_migration_backups(db_file: Path, keep: int = _KEEP_MIGRATION_BACKUPS) -> None:
    pattern = re.compile(re.escape(db_file.name) + r"\.bak-v(\d+)$")
    found: list[tuple[int, Path]] = []
    for item in db_file.parent.iterdir():
        match = pattern.match(item.name)
        if match:
            found.append((int(match.group(1)), item))
    found.sort()
    for _, item in found[:-keep] if keep > 0 else found:
        try:
            item.unlink()
        except OSError:
            log.warning("Could not delete old migration backup %s", item)


def migrate(conn: sqlite3.Connection, db_file: Path | None = None) -> int:
    """Bring the schema to SCHEMA_VERSION. Backs up an existing DB first (keep last 2).

    Returns the version before migrating. A database newer than this build is left untouched.
    """
    old = user_version(conn)
    if old >= SCHEMA_VERSION:
        if old > SCHEMA_VERSION:
            log.warning("Database schema v%d is newer than this build (v%d)", old, SCHEMA_VERSION)
        return old
    if old > 0 and db_file is not None:
        try:
            backup_to(conn, db_file.with_name(f"{db_file.name}.bak-v{old}"))
            _prune_migration_backups(db_file)
        except (OSError, sqlite3.Error):
            log.exception("Pre-migration backup failed")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for version in range(old + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={int(version)}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    log.info("Migrated database from v%d to v%d", old, SCHEMA_VERSION)
    return old


def quarantine(db_file: Path, clock: Clock | None = None) -> Path:
    """Rename a corrupt database (and sidecars) to ``madhura.corrupt-YYYYmmdd-HHMMSS.db``."""
    stamp = (clock or SystemClock()).now().strftime("%Y%m%d-%H%M%S")
    stem = db_file.stem
    dest = db_file.with_name(f"{stem}.corrupt-{stamp}{db_file.suffix}")
    counter = 1
    while dest.exists():
        dest = db_file.with_name(f"{stem}.corrupt-{stamp}-{counter}{db_file.suffix}")
        counter += 1
    if db_file.exists():
        os.replace(db_file, dest)
    for side in sidecar_paths(db_file)[1:]:
        if side.exists():
            try:
                os.replace(side, dest.with_name(dest.name + side.name[len(db_file.name):]))
            except OSError:
                log.warning("Could not move %s", side)
    return dest


def _open_checked(db_file: Path) -> sqlite3.Connection:
    conn = connect(db_file)
    try:
        if not quick_check(conn):
            raise sqlite3.DatabaseError("quick_check failed")
        migrate(conn, db_file)
    except BaseException:
        conn.close()
        raise
    return conn


def open_database(
    db_file: str | os.PathLike[str],
    clock: Clock | None = None,
    on_recovered: Callable[[Path], None] | None = None,
) -> tuple[sqlite3.Connection, Path | None]:
    """Open + verify + migrate *db_file*; on corruption quarantine it and start fresh.

    Returns ``(connection, quarantined_path_or_None)``. ``":memory:"`` is supported. A file that
    is only locked or unreachable is retried (:data:`OPEN_RETRY_DELAYS_S`) and then raises
    :class:`DatabaseUnavailable` without touching anything, so a healthy database is never
    mistaken for a damaged one.
    """
    if os.fspath(db_file) == ":memory:":
        conn = connect(":memory:")
        migrate(conn)
        return conn, None
    path = Path(db_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    delays = list(OPEN_RETRY_DELAYS_S)
    while True:
        try:
            return _open_checked(path), None
        except sqlite3.DatabaseError as exc:
            if not is_unavailable_error(exc):
                log.exception("Database %s is unreadable; starting fresh", path)
                break
            if not delays or _is_busy(exc):
                log.error("Database %s can't be opened right now: %s", path, exc)
                raise DatabaseUnavailable(path, exc) from exc
            log.warning("Database %s can't be opened right now (%s); retrying", path, exc)
            time.sleep(delays.pop(0))
    try:
        moved = quarantine(path, clock)
    except OSError as exc:  # e.g. Windows: another process still has the file open
        log.error("Could not move the damaged database %s aside: %s", path, exc)
        raise DatabaseUnavailable(path, exc) from exc
    conn = connect(path)
    migrate(conn, path)
    if on_recovered is not None:
        on_recovered(moved)
    return conn, moved


def checkpoint_and_close(conn: sqlite3.Connection) -> None:
    """``PRAGMA wal_checkpoint(TRUNCATE)`` then close (errors are logged, never raised)."""
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        log.warning("WAL checkpoint failed", exc_info=True)
    try:
        conn.close()
    except sqlite3.Error:
        log.warning("Closing the database failed", exc_info=True)
