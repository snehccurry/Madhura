"""Seed realistic demo data into a Madhura database.

Used for README screenshots, manual QA and UI tests::

    python tools/seed_demo.py --data-dir /tmp/madhura-demo [--weeks 8] [--now 2026-10-14T15:30] [--seed 7]
    MADHURA_DATA_DIR=/tmp/madhura-demo python -m madhura

or from Python::

    from tools.seed_demo import seed_demo
    seed_demo(repo, clock.now(), weeks=8)

The data is deterministic for a given ``seed`` and ``clock_now``: focus sessions over the last
*weeks* weeks (varied projects, tasks, hours and outcomes; a gentle upward trend), check-ins with
drift reasons, notes, Up next tasks (today, tomorrow and a couple carried over), parking-lot
captures, habits with logs and a few day notes. Nothing is ever created after ``clock_now`` and no
session is left 'active'.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from madhura.core.days import logical_date, next_work_day  # noqa: E402

__all__ = ["seed_demo", "main", "PROJECTS", "HABITS"]

#: project → task titles.
PROJECTS: dict[str | None, tuple[str, ...]] = {
    "Madhura": (
        "Refactor the session repository",
        "Polish the insights charts",
        "Fix the tray icon at 125% scaling",
        "Write release notes for 2.0",
        "Review pull requests",
    ),
    "Thesis": (
        "Draft chapter 3: methodology",
        "Clean the survey dataset",
        "Read: Deep Work, chapter 4",
        "Outline the literature review",
    ),
    "Northwind": (
        "Prepare slides for Thursday's sync",
        "Quarterly report — revenue section",
        "Answer the onboarding questions",
    ),
    "Learning": (
        "Rust ownership exercises",
        "Watch the SQLite internals talk",
    ),
    None: (
        "Inbox zero",
        "Plan the week",
        "Tidy up notes",
    ),
}

_PROJECT_WEIGHTS: dict[str | None, int] = {"Madhura": 34, "Thesis": 26, "Northwind": 20, "Learning": 10, None: 10}

_DRIFT_REASONS: tuple[tuple[str, int], ...] = (
    ("Phone", 30),
    ("Messages", 20),
    ("Social media", 15),
    ("Email", 15),
    ("Browsing", 10),
    ("Daydreaming", 7),
    ("Other", 3),
)

_NOTES = (
    "Good flow once notifications were off.",
    "Got stuck on the edge case for DST — continue tomorrow.",
    "Shorter than planned, but the hard part is done.",
    "Music helped. Same playlist next time.",
    "Kept getting pulled into Slack.",
    "Finished early — nice.",
)

#: (name, emoji, cue, time, days mask, focus_minutes, project)
HABITS: tuple[tuple[str, str | None, str | None, str | None, int, int | None, str | None], ...] = (
    ("Work deeply on your main project", "🎯", "First thing after coffee", None, 31, 60, None),
    ("Ship something small", "🚀", None, "17:00", 31, None, None),
    ("Learn and apply one concept", "💡", "After lunch", "13:30", 127, None, None),
    ("Read 10 pages", "📖", "Before bed", "21:30", 127, None, None),
    ("Move your body", "🏃", "After work", "18:30", 0b1010101, None, None),
    ("Plan tomorrow", "📝", "Wrap-up", "17:45", 31, None, None),
)

_CAPTURES = (
    "Look into the flaky CI test on Windows",
    "Book the dentist",
    "Idea: weekly digest email for the team",
)


def _weighted(rng: random.Random, pairs: Sequence[tuple[Any, int]]) -> Any:
    total = sum(w for _v, w in pairs)
    pick = rng.uniform(0, total)
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if pick <= acc:
            return value
    return pairs[-1][0]


def _day_slots(rng: random.Random, workday: bool, intensity: float) -> list[time]:
    """Start times for one day's sessions."""
    if workday:
        n = max(0, int(round(rng.gauss(3.2 * intensity, 1.0))))
        pools = [(8, 30, 11, 30), (13, 30, 17, 15), (19, 30, 21, 30)]
        weights = [0.48, 0.42, 0.10]
    else:
        n = 0 if rng.random() < 0.55 else rng.choice((1, 1, 2))
        pools = [(9, 30, 12, 0), (15, 0, 18, 0), (20, 0, 22, 0)]
        weights = [0.45, 0.35, 0.20]
    slots: list[time] = []
    for _ in range(min(n, 6)):
        h1, m1, h2, m2 = _weighted(rng, list(zip(pools, [int(w * 100) for w in weights], strict=True)))
        start = h1 * 60 + m1
        end = h2 * 60 + m2
        minute = rng.randrange(start, end, 5)
        slots.append(time(minute // 60, minute % 60))
    slots.sort()
    # Keep sessions from overlapping: push each start after the previous one plus 75 min.
    out: list[time] = []
    last = -10_000
    for t in slots:
        m = max(t.hour * 60 + t.minute, last + 75)
        if m > 23 * 60 + 15:
            break
        out.append(time(m // 60, m % 60))
        last = m
    return out


def seed_demo(
    repo: Any,
    clock_now: datetime,
    weeks: int = 8,
    *,
    day_start_hour: int = 4,
    work_days: int = 31,
    seed: int = 7,
    habits: bool = True,
    tasks: bool = True,
    captures: bool = True,
) -> dict[str, int]:
    """Write demo data ending at *clock_now* into *repo*; returns ``{kind: rows added}``.

    Sessions cover the last *weeks* weeks (today only up to *clock_now*); every one is finished
    (completed ≈ 76 %, ended early ≈ 17 %, abandoned ≈ 7 %) with ~7-minute check-ins.
    """
    rng = random.Random(seed)
    now = clock_now.replace(microsecond=0)
    today = logical_date(now, day_start_hour)
    first = today - timedelta(days=max(1, int(weeks)) * 7 - 1)
    counts = {"sessions": 0, "checkins": 0, "tasks": 0, "captures": 0, "habits": 0, "habit_logs": 0, "day_notes": 0}
    project_pairs = list(_PROJECT_WEIGHTS.items())
    total_days = (today - first).days + 1

    with repo.transaction():
        for offset in range(total_days):
            d = first + timedelta(days=offset)
            workday = bool(work_days & (1 << d.weekday()))
            if workday and rng.random() < 0.07:
                continue  # an occasional day off
            progress = offset / max(1, total_days - 1)
            intensity = 0.75 + 0.45 * progress
            for slot in _day_slots(rng, workday, intensity):
                started = datetime.combine(d, slot)
                if started.hour < day_start_hour:
                    started += timedelta(days=1)
                planned_min = _weighted(rng, [(25, 22), (45, 30), (60, 34), (90, 14)])
                roll = rng.random()
                if roll < 0.07:
                    outcome = "abandoned"
                    focused = rng.randint(2, 9) * 60
                elif roll < 0.24:
                    outcome = "ended_early"
                    focused = int(planned_min * 60 * rng.uniform(0.4, 0.9))
                else:
                    outcome = "completed"
                    focused = planned_min * 60
                paused = rng.choice((0, 0, 0, 60, 120, 240, 420)) if outcome != "abandoned" else 0
                ended = started + timedelta(seconds=focused + paused)
                if ended > now:
                    continue
                project = _weighted(rng, project_pairs)
                task = rng.choice(PROJECTS[project])
                sid = repo.create_session(task, project, None, started, planned_min * 60)
                note = rng.choice(_NOTES) if outcome != "abandoned" and rng.random() < 0.14 else None
                repo.update_session(
                    sid,
                    ended_at=ended,
                    focused_seconds=focused,
                    paused_seconds=paused,
                    outcome=outcome,
                    note=note,
                )
                counts["sessions"] += 1
                counts["checkins"] += _seed_checkins(repo, rng, sid, started, focused, outcome)
        # A rare early-morning session in the oldest week (widens the 12-week heatmap to 00–23).
        early_day = first + timedelta(days=2)
        early = datetime.combine(early_day + timedelta(days=1), time(5, 10))
        if early < now and total_days > 14:
            sid = repo.create_session("Draft chapter 3: methodology", "Thesis", None, early, 45 * 60)
            repo.update_session(sid, ended_at=early + timedelta(minutes=45), focused_seconds=45 * 60, outcome="completed")
            counts["sessions"] += 1
            counts["checkins"] += _seed_checkins(repo, rng, sid, early, 45 * 60, "completed")

        if tasks:
            counts["tasks"] += _seed_tasks(repo, today, work_days)
        if captures:
            for i, text in enumerate(_CAPTURES):
                repo.add_capture(text, at=now - timedelta(hours=5 - i))
                counts["captures"] += 1
        if habits:
            h, logs = _seed_habits(repo, rng, first, today, now)
            counts["habits"] += h
            counts["habit_logs"] += logs
        for back, text in ((1, "Finally cracked the DST bug."), (3, "Two deep blocks before lunch.")):
            d = today - timedelta(days=back)
            if d >= first:
                repo.set_day_note(d, text)
                counts["day_notes"] += 1
    return counts


def _seed_checkins(repo: Any, rng: random.Random, sid: int, started: datetime, focused: int, outcome: str) -> int:
    n = 0
    interval = 7 * 60
    t = interval
    misses_in_row = 0
    while t < focused - 60:
        at = started + timedelta(seconds=t)
        roll = rng.random()
        if roll < 0.05 and misses_in_row == 0 and outcome != "abandoned":
            repo.add_checkin(sid, at, "missed")
            misses_in_row += 1
        elif roll < 0.21:
            reason = _weighted(rng, list(_DRIFT_REASONS)) if rng.random() < 0.8 else None
            repo.add_checkin(sid, at, "drifted", reason)
            misses_in_row = 0
        else:
            repo.add_checkin(sid, at, "focused")
            misses_in_row = 0
        n += 1
        t += interval
    return n


def _seed_tasks(repo: Any, today: date, work_days: int) -> int:
    n = 0
    plan = (
        ("Prepare slides for Thursday's sync", 45, "Northwind", False),
        ("Refactor the onboarding flow and remove the legacy settings code", 90, "Madhura", False),
        ("Read: Shape Up, chapter 3", 25, "Learning", True),
        ("Reply to the reviewer comments", 25, "Thesis", False),
    )
    for title, minutes, project, done in plan:
        tid = repo.add_task(title, minutes, project, plan_date=today)
        if done:
            repo.set_task_done(tid, True)
        n += 1
    carried = (("Update the README screenshots", 25, "Madhura"), ("Book the venue for the offsite", 15, None))
    for title, minutes, project in carried:
        repo.add_task(title, minutes, project, plan_date=today - timedelta(days=2))
        n += 1
    repo.add_task("Write the release announcement", 60, "Madhura", plan_date=next_work_day(today, work_days))
    return n + 1


def _seed_habits(repo: Any, rng: random.Random, first: date, today: date, now: datetime) -> tuple[int, int]:
    n_habits = n_logs = 0
    created = datetime.combine(first, time(9, 0))
    for name, emoji, cue, at, days, focus_minutes, project in HABITS:
        hid = repo.add_habit(name, emoji, cue, at, days, True, False, focus_minutes, project)
        repo.update_habit(hid, created_at=created)
        n_habits += 1
        rate = rng.uniform(0.62, 0.9)
        d = first
        while d <= today:
            if days & (1 << d.weekday()):
                due_passed = d < today or at is None or now.time() >= time.fromisoformat(at)
                roll = rng.random()
                if d == today and not due_passed:
                    pass
                elif roll < 0.05:
                    repo.set_habit_status(hid, d, "skipped", datetime.combine(d, time(8, 0)))
                    n_logs += 1
                elif roll < 0.05 + rate:
                    stamp = datetime.combine(d, time.fromisoformat(at) if at else time(10, 0))
                    repo.set_habit_status(hid, d, "done", min(stamp, now))
                    n_logs += 1
            d += timedelta(days=1)
    return n_habits, n_logs


# =========================================================================== CLI


def main(argv: Sequence[str] | None = None) -> int:
    """``python tools/seed_demo.py --data-dir DIR`` — seed DIR/madhura.db (refuses non-empty DBs)."""
    parser = argparse.ArgumentParser(description="Seed realistic Madhura demo data.")
    parser.add_argument("--data-dir", required=True, help="Madhura data directory (madhura.db + settings.json)")
    parser.add_argument("--weeks", type=int, default=8, help="weeks of history (default 8)")
    parser.add_argument("--now", default=None, help="pretend 'now' as ISO datetime (default: the current time)")
    parser.add_argument("--seed", type=int, default=7, help="random seed (default 7)")
    parser.add_argument("--force", action="store_true", help="seed even if the database already has sessions")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    from madhura.core.clock import FakeClock, SystemClock
    from madhura.data.repository import Repository

    now = datetime.fromisoformat(args.now) if args.now else SystemClock().now()
    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    repo = Repository(data_dir / "madhura.db", clock=FakeClock(now))
    try:
        if repo.recent_sessions(limit=1) and not args.force:
            print(f"{data_dir / 'madhura.db'} already has sessions; pass --force to add demo data anyway.", file=sys.stderr)
            return 1
        counts = seed_demo(repo, now, weeks=args.weeks, seed=args.seed)
    finally:
        repo.close()
    _mark_onboarded(data_dir / "settings.json")
    summary = ", ".join(f"{v} {k}" for k, v in counts.items())
    print(f"Seeded {data_dir}: {summary}")
    return 0


def _mark_onboarded(path: Path) -> None:
    """Set onboarded=True (so the app opens straight to the Focus page)."""
    from PySide6.QtCore import QCoreApplication

    from madhura.data.settings import SettingsStore

    _app = QCoreApplication.instance() or QCoreApplication([])
    store = SettingsStore(path)
    store.update(onboarded=True)
    store.flush()
    del _app


if __name__ == "__main__":
    raise SystemExit(main())
