"""Repository — the only place with SQL (spec §4). All reads return models from core.models."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from madhura import __version__
from madhura.core.clock import Clock, SystemClock
from madhura.core.days import logical_date
from madhura.core.models import (
    COUNTED_OUTCOMES,
    Capture,
    CheckIn,
    CheckInResponse,
    Habit,
    HabitLog,
    HabitStatus,
    Outcome,
    PlannedTask,
    Session,
    from_date_str,
    from_iso,
    to_date_str,
    to_iso,
)
from madhura.data import db as dbmod

__all__ = ["Repository", "ENGINE_SNAPSHOT_KEY", "RECOVERED_FROM_KEY"]

log = logging.getLogger(__name__)

ENGINE_SNAPSHOT_KEY = "engine_snapshot"
RECOVERED_FROM_KEY = "recovered_from"

_SESSION_FIELDS = {
    "task",
    "project",
    "planned_task_id",
    "started_at",
    "ended_at",
    "planned_seconds",
    "focused_seconds",
    "paused_seconds",
    "outcome",
    "note",
}
_SESSION_INT_FIELDS = {"planned_seconds", "focused_seconds", "paused_seconds"}
_TASK_FIELDS = {"title", "project", "minutes", "plan_date", "position", "done", "created_at", "completed_at"}
_HABIT_FIELDS = {
    "name",
    "emoji",
    "cue",
    "time",
    "days",
    "reminder",
    "urgent",
    "focus_minutes",
    "project",
    "position",
    "archived",
    "created_at",
}
_TABLES = ("checkins", "captures", "habit_logs", "sessions", "tasks", "habits", "day_notes", "kv")
_RESPONSES = {r.value for r in CheckInResponse}
_STATUSES = {s.value for s in HabitStatus}


def _sql_value(value: Any) -> Any:
    """Convert Python values to SQLite storage values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return to_iso(value)
    if isinstance(value, date):
        return to_date_str(value)
    if isinstance(value, bool):
        return int(value)
    return value


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class Repository:
    """Data access over one SQLite connection (autocommit; use :meth:`transaction` for batches).

    ``Repository(path)`` opens/verifies/migrates the database file (corrupt files are quarantined
    and ``kv['recovered_from']`` is set); ``Repository(":memory:")`` and ``Repository(conn)``
    are also accepted. *today_fn* supplies the logical date when ``add_task`` gets no plan_date.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | sqlite3.Connection,
        clock: Clock | None = None,
        today_fn: Callable[[], date] | None = None,
    ) -> None:
        self.clock: Clock = clock or SystemClock()
        self.today_fn = today_fn
        self.recovered_from: Path | None = None
        self.path: Path | None = None
        self._tx_depth = 0
        self._closed = False
        if isinstance(path, sqlite3.Connection):
            self.conn = path
            self.conn.row_factory = sqlite3.Row
            dbmod.migrate(self.conn)
        else:
            if os.fspath(path) != ":memory:":
                self.path = Path(path)
            self.conn, self.recovered_from = dbmod.open_database(path, self.clock)
            if self.recovered_from is not None:
                self.kv_set(RECOVERED_FROM_KEY, self.recovered_from.name)

    @classmethod
    def open(cls, path: str | os.PathLike[str], clock: Clock | None = None) -> Repository:
        """Alias for ``Repository(path, clock)``."""
        return cls(path, clock)

    # ================================================================== infra

    def _now(self) -> datetime:
        return self.clock.now().replace(microsecond=0)

    def _today(self) -> date:
        if self.today_fn is not None:
            return self.today_fn()
        return logical_date(self.clock.now(), 4)

    def _exec(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def _all(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[Repository]:
        """``BEGIN IMMEDIATE``/``COMMIT``/``ROLLBACK``; nested use becomes a SAVEPOINT."""
        depth = self._tx_depth
        if depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT sp{depth}")
        self._tx_depth += 1
        try:
            yield self
        except BaseException:
            self._tx_depth -= 1
            try:
                if depth == 0:
                    self.conn.execute("ROLLBACK")
                else:
                    self.conn.execute(f"ROLLBACK TO sp{depth}")
                    self.conn.execute(f"RELEASE sp{depth}")
            except sqlite3.Error:
                log.exception("Rollback failed")
            raise
        else:
            self._tx_depth -= 1
            if depth == 0:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute(f"RELEASE sp{depth}")

    @property
    def in_transaction(self) -> bool:
        """True inside :meth:`transaction`."""
        return self._tx_depth > 0

    @property
    def closed(self) -> bool:
        """True after :meth:`close`."""
        return self._closed

    def close(self) -> None:
        """Checkpoint the WAL (TRUNCATE) and close. Idempotent."""
        if self._closed:
            return
        self._closed = True
        dbmod.checkpoint_and_close(self.conn)

    # ================================================================== row mapping

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        try:
            outcome = Outcome(row["outcome"])
        except ValueError:
            outcome = Outcome.ENDED_EARLY
        return Session(
            id=row["id"],
            task=row["task"],
            project=row["project"],
            planned_task_id=row["planned_task_id"],
            started_at=from_iso(row["started_at"]),  # type: ignore[arg-type]
            ended_at=from_iso(row["ended_at"]),
            planned_seconds=int(row["planned_seconds"]),
            focused_seconds=int(row["focused_seconds"]),
            paused_seconds=int(row["paused_seconds"]),
            outcome=outcome,
            note=row["note"],
        )

    @staticmethod
    def _checkin(row: sqlite3.Row) -> CheckIn:
        return CheckIn(
            id=row["id"],
            session_id=row["session_id"],
            at=from_iso(row["at"]),  # type: ignore[arg-type]
            response=CheckInResponse(row["response"]),
            reason=row["reason"],
        )

    @staticmethod
    def _task(row: sqlite3.Row) -> PlannedTask:
        return PlannedTask(
            id=row["id"],
            title=row["title"],
            project=row["project"],
            minutes=int(row["minutes"]),
            plan_date=from_date_str(row["plan_date"]),  # type: ignore[arg-type]
            position=int(row["position"]),
            done=bool(row["done"]),
            created_at=from_iso(row["created_at"]),  # type: ignore[arg-type]
            completed_at=from_iso(row["completed_at"]),
        )

    @staticmethod
    def _habit(row: sqlite3.Row) -> Habit:
        return Habit(
            id=row["id"],
            name=row["name"],
            emoji=row["emoji"],
            cue=row["cue"],
            time=row["time"],
            days=int(row["days"]),
            reminder=bool(row["reminder"]),
            urgent=bool(row["urgent"]),
            focus_minutes=row["focus_minutes"],
            project=row["project"],
            position=int(row["position"]),
            archived=bool(row["archived"]),
            created_at=from_iso(row["created_at"]),  # type: ignore[arg-type]
        )

    @staticmethod
    def _habit_log(row: sqlite3.Row) -> HabitLog:
        return HabitLog(
            id=row["id"],
            habit_id=row["habit_id"],
            date=from_date_str(row["date"]),  # type: ignore[arg-type]
            status=HabitStatus(row["status"]),
            at=from_iso(row["at"]),  # type: ignore[arg-type]
        )

    @staticmethod
    def _capture(row: sqlite3.Row) -> Capture:
        return Capture(
            id=row["id"],
            session_id=row["session_id"],
            text=row["text"],
            at=from_iso(row["at"]),  # type: ignore[arg-type]
            done=bool(row["done"]),
        )

    def _update(self, table: str, allowed: set[str], row_id: int, fields: Mapping[str, Any]) -> None:
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown {table} field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        names = sorted(fields)
        assignments = ", ".join(f"{name} = ?" for name in names)
        values = [_sql_value(fields[name]) for name in names]
        self._exec(f"UPDATE {table} SET {assignments} WHERE id = ?", [*values, int(row_id)])

    # ================================================================== sessions

    def create_session(
        self,
        task: str,
        project: str | None,
        planned_task_id: int | None,
        started_at: datetime,
        planned_seconds: int,
    ) -> int:
        """Insert an 'active' session row; returns its id."""
        cur = self._exec(
            "INSERT INTO sessions(task, project, planned_task_id, started_at, planned_seconds, outcome)"
            " VALUES (?, ?, ?, ?, ?, 'active')",
            (task, _opt_str(project), planned_task_id, to_iso(started_at), int(round(planned_seconds))),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def update_session(self, session_id: int, **fields: Any) -> None:
        """Update columns of a session (datetimes/enums converted; *_seconds rounded to int)."""
        for name in _SESSION_INT_FIELDS & set(fields):
            if fields[name] is not None:
                fields[name] = int(round(float(fields[name])))
        if "outcome" in fields:
            fields["outcome"] = Outcome(_sql_value(fields["outcome"]))
        if "project" in fields:
            fields["project"] = _opt_str(fields["project"])
        self._update("sessions", _SESSION_FIELDS, session_id, fields)

    def get_session(self, session_id: int) -> Session | None:
        """Session by id, or None."""
        row = self._one("SELECT * FROM sessions WHERE id = ?", (int(session_id),))
        return self._session(row) if row else None

    def delete_session(self, session_id: int) -> None:
        """Delete a session (its check-ins cascade; captures keep their text with session_id NULL)."""
        self._exec("DELETE FROM sessions WHERE id = ?", (int(session_id),))

    def sessions_between(
        self,
        start_dt: datetime,
        end_dt: datetime,
        outcomes: Iterable[str] | None = COUNTED_OUTCOMES,
    ) -> list[Session]:
        """Sessions with ``start_dt <= started_at < end_dt`` (oldest first). ``outcomes=None`` → all."""
        sql = "SELECT * FROM sessions WHERE started_at >= ? AND started_at < ?"
        params: list[Any] = [to_iso(start_dt), to_iso(end_dt)]
        if outcomes is not None:
            values = [_sql_value(o) for o in outcomes]
            if not values:
                return []
            sql += f" AND outcome IN ({', '.join('?' for _ in values)})"
            params.extend(values)
        sql += " ORDER BY started_at, id"
        return [self._session(r) for r in self._all(sql, params)]

    def recent_sessions(
        self,
        limit: int = 50,
        start_dt: datetime | None = None,
        end_dt: datetime | None = None,
        include_abandoned: bool = True,
    ) -> list[Session]:
        """Finished sessions, newest first (active rows are never included)."""
        clauses = ["outcome != 'active'"]
        params: list[Any] = []
        if not include_abandoned:
            clauses.append("outcome != 'abandoned'")
        if start_dt is not None:
            clauses.append("started_at >= ?")
            params.append(to_iso(start_dt))
        if end_dt is not None:
            clauses.append("started_at < ?")
            params.append(to_iso(end_dt))
        params.append(int(limit))
        sql = f"SELECT * FROM sessions WHERE {' AND '.join(clauses)} ORDER BY started_at DESC, id DESC LIMIT ?"
        return [self._session(r) for r in self._all(sql, params)]

    def recent_task_titles(self, limit: int = 50) -> list[str]:
        """Distinct task titles from sessions and Up next, most recently used first."""
        rows = self._all(
            """
            SELECT title, MAX(at) AS last FROM (
                SELECT task AS title, started_at AS at FROM sessions
                UNION ALL
                SELECT title, created_at AS at FROM tasks
            )
            WHERE TRIM(title) != ''
            GROUP BY title
            ORDER BY last DESC, title
            LIMIT ?
            """,
            (int(limit),),
        )
        return [r["title"] for r in rows]

    def projects(self) -> list[str]:
        """Distinct non-empty project names (sessions, tasks, habits), most recently used first."""
        rows = self._all(
            """
            SELECT project, MAX(at) AS last FROM (
                SELECT project, started_at AS at FROM sessions
                UNION ALL
                SELECT project, created_at AS at FROM tasks
                UNION ALL
                SELECT project, created_at AS at FROM habits
            )
            WHERE project IS NOT NULL AND TRIM(project) != ''
            GROUP BY project
            ORDER BY last DESC, project
            """
        )
        return [r["project"] for r in rows]

    def active_sessions(self) -> list[Session]:
        """Rows still marked 'active' (normally at most the engine's current one)."""
        return [self._session(r) for r in self._all("SELECT * FROM sessions WHERE outcome = 'active' ORDER BY id")]

    def save_engine_checkpoint(
        self,
        snapshot: dict[str, Any] | None,
        session_id: int | None,
        **session_fields: Any,
    ) -> None:
        """Write kv['engine_snapshot'] (None clears it) and the session row in ONE transaction."""
        with self.transaction():
            self.kv_set(ENGINE_SNAPSHOT_KEY, snapshot)
            if session_id is not None and session_fields:
                self.update_session(session_id, **session_fields)

    # ================================================================== check-ins

    def add_checkin(self, session_id: int, at: datetime, response: str, reason: str | None = None) -> int:
        """Record a check-in answer ('focused' | 'drifted' | 'missed'); returns its id."""
        value = _sql_value(response)
        if value not in _RESPONSES:
            raise ValueError(f"invalid check-in response {response!r}")
        cur = self._exec(
            "INSERT INTO checkins(session_id, at, response, reason) VALUES (?, ?, ?, ?)",
            (int(session_id), to_iso(at), value, _opt_str(reason)),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def checkins_for_session(self, session_id: int) -> list[CheckIn]:
        """Check-ins of one session, oldest first."""
        rows = self._all("SELECT * FROM checkins WHERE session_id = ? ORDER BY at, id", (int(session_id),))
        return [self._checkin(r) for r in rows]

    def checkins_between(self, start_dt: datetime, end_dt: datetime) -> list[CheckIn]:
        """Check-ins with ``start_dt <= at < end_dt``, oldest first."""
        rows = self._all(
            "SELECT * FROM checkins WHERE at >= ? AND at < ? ORDER BY at, id",
            (to_iso(start_dt), to_iso(end_dt)),
        )
        return [self._checkin(r) for r in rows]

    def checkin_counts(self, session_id: int) -> dict[str, int]:
        """``{"focused": n, "drifted": n, "missed": n}`` for one session."""
        counts = {r.value: 0 for r in CheckInResponse}
        rows = self._all(
            "SELECT response, COUNT(*) AS n FROM checkins WHERE session_id = ? GROUP BY response",
            (int(session_id),),
        )
        for row in rows:
            counts[row["response"]] = int(row["n"])
        return counts

    # ================================================================== tasks (Up next)

    def _next_task_position(self, d: date) -> int:
        row = self._one("SELECT MAX(position) AS p FROM tasks WHERE plan_date = ?", (to_date_str(d),))
        return 0 if row is None or row["p"] is None else int(row["p"]) + 1

    def add_task(
        self,
        title: str,
        minutes: int = 25,
        project: str | None = None,
        plan_date: date | None = None,
        position: int | None = None,
    ) -> int:
        """Add an Up next task (appended unless *position* given); returns its id."""
        title = (title or "").strip()
        if not title:
            raise ValueError("task title must not be empty")
        d = plan_date if plan_date is not None else self._today()
        with self.transaction():
            if position is None:
                position = self._next_task_position(d)
            else:
                self._exec(
                    "UPDATE tasks SET position = position + 1 WHERE plan_date = ? AND position >= ?",
                    (to_date_str(d), int(position)),
                )
            cur = self._exec(
                "INSERT INTO tasks(title, project, minutes, plan_date, position, done, created_at)"
                " VALUES (?, ?, ?, ?, ?, 0, ?)",
                (title, _opt_str(project), int(minutes), to_date_str(d), int(position), to_iso(self._now())),
            )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def tasks_for_date(self, d: date, include_done: bool = True) -> list[PlannedTask]:
        """Tasks planned for *d*, by position."""
        sql = "SELECT * FROM tasks WHERE plan_date = ?"
        if not include_done:
            sql += " AND done = 0"
        sql += " ORDER BY position, id"
        return [self._task(r) for r in self._all(sql, (to_date_str(d),))]

    def get_task(self, task_id: int) -> PlannedTask | None:
        """Task by id, or None."""
        row = self._one("SELECT * FROM tasks WHERE id = ?", (int(task_id),))
        return self._task(row) if row else None

    def update_task(self, task_id: int, **fields: Any) -> None:
        """Update task columns (title, project, minutes, plan_date, position, done, ...)."""
        if "project" in fields:
            fields["project"] = _opt_str(fields["project"])
        self._update("tasks", _TASK_FIELDS, task_id, fields)

    def delete_task(self, task_id: int) -> None:
        """Delete a task."""
        self._exec("DELETE FROM tasks WHERE id = ?", (int(task_id),))

    def set_task_done(self, task_id: int, done: bool, at: datetime | None = None) -> None:
        """Mark done (completed_at = *at* or now) or undone (completed_at cleared)."""
        completed = to_iso(at or self._now()) if done else None
        self._exec(
            "UPDATE tasks SET done = ?, completed_at = ? WHERE id = ?",
            (int(bool(done)), completed, int(task_id)),
        )

    def unfinished_before(self, d: date) -> list[PlannedTask]:
        """Not-done tasks planned before *d* (carry-over), oldest date first."""
        rows = self._all(
            "SELECT * FROM tasks WHERE done = 0 AND plan_date < ? ORDER BY plan_date, position, id",
            (to_date_str(d),),
        )
        return [self._task(r) for r in rows]

    def move_tasks_to_date(self, ids: Iterable[int], d: date) -> None:
        """Move tasks to *d*, appended after existing ones in the given order."""
        with self.transaction():
            position = self._next_task_position(d)
            for task_id in ids:
                self._exec(
                    "UPDATE tasks SET plan_date = ?, position = ? WHERE id = ?",
                    (to_date_str(d), position, int(task_id)),
                )
                position += 1

    def reorder_tasks(self, ids: Sequence[int]) -> None:
        """Set positions 0..n-1 following *ids*."""
        with self.transaction():
            for position, task_id in enumerate(ids):
                self._exec("UPDATE tasks SET position = ? WHERE id = ?", (position, int(task_id)))

    # ================================================================== habits

    def add_habit(
        self,
        name: str,
        emoji: str | None = None,
        cue: str | None = None,
        time: str | None = None,
        days: int = 127,
        reminder: bool = True,
        urgent: bool = False,
        focus_minutes: int | None = None,
        project: str | None = None,
    ) -> int:
        """Add a habit at the end of the list; returns its id."""
        name = (name or "").strip()
        if not name:
            raise ValueError("habit name must not be empty")
        row = self._one("SELECT MAX(position) AS p FROM habits")
        position = 0 if row is None or row["p"] is None else int(row["p"]) + 1
        cur = self._exec(
            "INSERT INTO habits(name, emoji, cue, time, days, reminder, urgent, focus_minutes, project,"
            " position, archived, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                name,
                _opt_str(emoji),
                _opt_str(cue),
                _opt_str(time),
                int(days),
                int(bool(reminder)),
                int(bool(urgent)),
                None if focus_minutes is None else int(focus_minutes),
                _opt_str(project),
                position,
                to_iso(self._now()),
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def update_habit(self, habit_id: int, **fields: Any) -> None:
        """Update habit columns."""
        for key in ("emoji", "cue", "time", "project"):
            if key in fields:
                fields[key] = _opt_str(fields[key])
        self._update("habits", _HABIT_FIELDS, habit_id, fields)

    def get_habit(self, habit_id: int) -> Habit | None:
        """Habit by id, or None."""
        row = self._one("SELECT * FROM habits WHERE id = ?", (int(habit_id),))
        return self._habit(row) if row else None

    def delete_habit(self, habit_id: int) -> None:
        """Delete a habit and (cascade) its logs."""
        self._exec("DELETE FROM habits WHERE id = ?", (int(habit_id),))

    def habits(self, include_archived: bool = False) -> list[Habit]:
        """Habits by position."""
        sql = "SELECT * FROM habits"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY position, id"
        return [self._habit(r) for r in self._all(sql)]

    def habit_logs_between(self, start_d: date, end_d: date) -> list[HabitLog]:
        """Habit logs with ``start_d <= date <= end_d`` (INCLUSIVE), by date."""
        rows = self._all(
            "SELECT * FROM habit_logs WHERE date >= ? AND date <= ? ORDER BY date, habit_id",
            (to_date_str(start_d), to_date_str(end_d)),
        )
        return [self._habit_log(r) for r in rows]

    def habit_logs_for_date(self, d: date) -> list[HabitLog]:
        """Habit logs of one date."""
        rows = self._all("SELECT * FROM habit_logs WHERE date = ? ORDER BY habit_id", (to_date_str(d),))
        return [self._habit_log(r) for r in rows]

    def set_habit_status(self, habit_id: int, d: date, status: str | None, at: datetime | None = None) -> None:
        """Upsert 'done'/'skipped' for (habit, date); None deletes the log."""
        if status is None:
            self._exec("DELETE FROM habit_logs WHERE habit_id = ? AND date = ?", (int(habit_id), to_date_str(d)))
            return
        value = _sql_value(status)
        if value not in _STATUSES:
            raise ValueError(f"invalid habit status {status!r}")
        self._exec(
            "INSERT INTO habit_logs(habit_id, date, status, at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(habit_id, date) DO UPDATE SET status = excluded.status, at = excluded.at",
            (int(habit_id), to_date_str(d), value, to_iso(at or self._now())),
        )

    # ================================================================== captures

    def add_capture(self, text: str, session_id: int | None = None, at: datetime | None = None) -> int:
        """Add a parking-lot capture; returns its id."""
        text = (text or "").strip()
        if not text:
            raise ValueError("capture text must not be empty")
        cur = self._exec(
            "INSERT INTO captures(session_id, text, at, done) VALUES (?, ?, ?, 0)",
            (session_id, text, to_iso(at or self._now())),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def open_captures(self) -> list[Capture]:
        """Captures not yet cleared, oldest first."""
        return [self._capture(r) for r in self._all("SELECT * FROM captures WHERE done = 0 ORDER BY at, id")]

    def set_capture_done(self, capture_id: int, done: bool) -> None:
        """Clear (done=True) or restore a capture."""
        self._exec("UPDATE captures SET done = ? WHERE id = ?", (int(bool(done)), int(capture_id)))

    def delete_capture(self, capture_id: int) -> None:
        """Delete a capture."""
        self._exec("DELETE FROM captures WHERE id = ?", (int(capture_id),))

    # ================================================================== day notes

    def get_day_note(self, d: date) -> str | None:
        """Note for a logical date, or None."""
        row = self._one("SELECT note FROM day_notes WHERE date = ?", (to_date_str(d),))
        return row["note"] if row else None

    def set_day_note(self, d: date, text: str | None) -> None:
        """Set the note for *d*; None/blank deletes it."""
        text = (text or "").strip()
        if not text:
            self._exec("DELETE FROM day_notes WHERE date = ?", (to_date_str(d),))
            return
        self._exec(
            "INSERT INTO day_notes(date, note) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET note = excluded.note",
            (to_date_str(d), text),
        )

    # ================================================================== kv

    def kv_get(self, key: str, default: Any = None) -> Any:
        """JSON-decoded value for *key*, or *default* when missing/undecodable."""
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            log.warning("kv[%s] is not valid JSON; ignoring", key)
            return default

    def kv_set(self, key: str, value: Any) -> None:
        """Store a JSON-serializable value (None deletes the key)."""
        if value is None:
            self._exec("DELETE FROM kv WHERE key = ?", (key,))
            return
        payload = json.dumps(value, ensure_ascii=False, default=_json_default)
        self._exec(
            "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload),
        )

    # ================================================================== maintenance

    def export_dict(self) -> dict[str, Any]:
        """Every table as plain JSON-ready rows (kv values decoded)."""
        out: dict[str, Any] = {
            "app": "Madhura",
            "version": __version__,
            "schema_version": dbmod.user_version(self.conn),
            "exported_at": to_iso(self._now()),
        }
        for table in ("sessions", "checkins", "tasks", "habits", "habit_logs", "captures", "day_notes"):
            order = "date" if table == "day_notes" else "id"
            out[table] = [dict(r) for r in self._all(f"SELECT * FROM {table} ORDER BY {order}")]
        kv: dict[str, Any] = {}
        for row in self._all("SELECT key, value FROM kv ORDER BY key"):
            try:
                kv[row["key"]] = json.loads(row["value"])
            except ValueError:
                kv[row["key"]] = row["value"]
        out["kv"] = kv
        return out

    def reset_all(self) -> None:
        """Delete all user data (every table, kv too except 'recovered_from')."""
        with self.transaction():
            for table in _TABLES:
                if table == "kv":
                    self._exec("DELETE FROM kv WHERE key != ?", (RECOVERED_FROM_KEY,))
                else:
                    self._exec(f"DELETE FROM {table}")

    def counted_seconds_between(self, start_dt: datetime, end_dt: datetime, project: str | None = None) -> int:
        """Sum of focused_seconds of completed/ended-early sessions started in ``[start, end)``."""
        sql = (
            "SELECT COALESCE(SUM(focused_seconds), 0) AS s FROM sessions"
            " WHERE started_at >= ? AND started_at < ? AND outcome IN ('completed', 'ended_early')"
        )
        params: list[Any] = [to_iso(start_dt), to_iso(end_dt)]
        if project is not None:
            sql += " AND project = ?"
            params.append(project)
        row = self._one(sql, params)
        return int(row["s"]) if row else 0


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return to_iso(value)
    if isinstance(value, date):
        return to_date_str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
