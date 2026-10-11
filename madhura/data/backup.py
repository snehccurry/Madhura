"""Database backups and staged restore (spec §2, §4)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import urllib.parse
from datetime import date
from pathlib import Path

from madhura.core.clock import Clock, SystemClock
from madhura.core.days import logical_date
from madhura.data import db as dbmod

__all__ = [
    "DAILY_KEEP",
    "backup_now",
    "daily_backup",
    "list_backups",
    "staged_restore_path",
    "stage_restore",
    "apply_staged_restore",
]

log = logging.getLogger(__name__)

DAILY_KEEP = 7
_DAILY_RE = re.compile(r"^madhura-(\d{4}-\d{2}-\d{2})\.db$")


def _backups_dir(dest_dir: Path | None) -> Path:
    if dest_dir is not None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir
    from madhura import paths

    return paths.backups_dir()


def backup_now(conn: sqlite3.Connection, dest_dir: Path | None = None, tag: str = "manual") -> Path:
    """Consistent copy of the live DB to ``<dest_dir>/madhura-<tag>.db`` (overwrites same tag)."""
    safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag)).strip("-") or "manual"
    dest = _backups_dir(dest_dir) / f"madhura-{safe_tag}.db"
    dbmod.backup_to(conn, dest)
    log.info("Backed up database to %s", dest)
    return dest


def list_backups(dest_dir: Path | None = None) -> list[Path]:
    """All ``*.db`` files in the backups directory, newest (by mtime) first."""
    folder = _backups_dir(dest_dir)
    items = [p for p in folder.glob("*.db") if p.is_file()]
    return sorted(items, key=lambda p: p.stat().st_mtime, reverse=True)


def daily_backup(
    conn: sqlite3.Connection,
    today: date | None = None,
    dest_dir: Path | None = None,
    keep: int = DAILY_KEEP,
) -> Path | None:
    """Back up once per (logical) day to ``backups/madhura-YYYY-MM-DD.db`` and keep the newest *keep*.

    Returns the new file, or None when today's backup already exists or the backup failed.
    """
    day = today if today is not None else logical_date(SystemClock().now(), 4)
    folder = _backups_dir(dest_dir)
    dest = folder / f"madhura-{day.isoformat()}.db"
    result: Path | None = None
    if not dest.exists():
        try:
            result = backup_now(conn, folder, day.isoformat())
        except (OSError, sqlite3.Error):
            log.exception("Daily backup failed")
            return None
    dailies = sorted(p for p in folder.iterdir() if _DAILY_RE.match(p.name))
    for old in dailies[: max(0, len(dailies) - keep)]:
        try:
            old.unlink()
        except OSError:
            log.warning("Could not delete old backup %s", old)
    return result


def staged_restore_path(db_path: Path | None = None) -> Path:
    """``<db>.restore`` next to the database."""
    if db_path is None:
        from madhura import paths

        db_path = paths.db_path()
    return db_path.with_name(db_path.name + ".restore")


def _read_only_uri(path: Path) -> str:
    """``file:`` URI opening *path* read-only.

    Percent-encoded, so '#', '?' and '%' in folder names work. Empty authority:
    ``file:///home/…``, ``file:///C:/…`` (SQLite drops the '/' before a drive letter) and
    ``file:////server/share/…`` for UNC paths.
    """
    posix = path.absolute().as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix
    return f"file://{urllib.parse.quote(posix, safe='/:')}?mode=ro"


def _verify_sqlite(path: Path) -> None:
    try:
        conn = sqlite3.connect(_read_only_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"{path.name} is not a Madhura database") from exc
    try:
        ok = dbmod.quick_check(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as exc:
        raise ValueError(f"{path.name} is not a Madhura database") from exc
    finally:
        conn.close()
    if not ok or "sessions" not in tables or "kv" not in tables:
        raise ValueError(f"{path.name} is not a Madhura database")


def stage_restore(path: str | os.PathLike[str], db_path: Path | None = None) -> Path:
    """Validate a backup and copy it to ``madhura.db.restore``; it is swapped in at next startup.

    Raises ValueError when *path* is not a readable Madhura database.
    """
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"{source} does not exist")
    _verify_sqlite(source)
    target = staged_restore_path(db_path)
    # An online copy, not a file copy: commits still in the source's -wal (a madhura.db copied
    # from a running/crashed app, a quarantined file) are part of what was just verified and
    # must be part of what is restored. The result is one standalone file (atomic replace).
    try:
        src = sqlite3.connect(_read_only_uri(source), uri=True)
        try:
            dbmod.backup_to(src, target)
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise ValueError(f"{source.name} could not be read") from exc
    log.info("Staged %s for restore", source)
    return target


def apply_staged_restore(db_path: Path | None = None, clock: Clock | None = None) -> bool:
    """If ``madhura.db.restore`` exists, swap it in (the old DB is kept in backups/). Call before connecting.

    Returns True when a restore was applied. Never raises; failures are logged.
    """
    if db_path is None:
        from madhura import paths

        db_path = paths.db_path()
    staged = staged_restore_path(db_path)
    if not staged.exists():
        return False
    try:
        if db_path.exists():
            stamp = (clock or SystemClock()).now().strftime("%Y%m%d-%H%M%S")
            keep_path = _backups_dir(db_path.parent / "backups") / f"madhura-pre-restore-{stamp}.db"
            try:
                old = dbmod.connect(db_path)
                try:
                    dbmod.backup_to(old, keep_path)
                finally:
                    old.close()
            except (sqlite3.Error, OSError):
                log.warning("Old database unreadable; moving it aside as-is")
                shutil.copyfile(db_path, keep_path)
            for item in dbmod.sidecar_paths(db_path):
                if item.exists():
                    item.unlink()
        os.replace(staged, db_path)
        log.info("Restored database from staged backup")
        return True
    except OSError:
        log.exception("Applying the staged restore failed")
        return False
