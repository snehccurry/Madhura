"""tools/seed_demo.py [I]: realistic, deterministic demo data (and its CLI)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from madhura.core.clock import FakeClock
from madhura.core.days import logical_date
from madhura.data.repository import Repository
from test_i_support import load_seed_demo

NOW = datetime(2026, 10, 14, 15, 30)


@pytest.fixture(scope="module")
def seed():  # noqa: ANN201
    return load_seed_demo()


def _repo(path: Path) -> Repository:
    return Repository(path, clock=FakeClock(NOW))


def test_seed_demo_contents(seed, tmp_path: Path) -> None:  # noqa: ANN001
    repo = _repo(tmp_path / "a.db")
    counts = seed.seed_demo(repo, NOW, weeks=8)
    try:
        assert counts["sessions"] > 60 and counts["checkins"] > counts["sessions"]
        sessions = repo.recent_sessions(limit=100_000)
        assert len(sessions) == counts["sessions"]
        assert repo.active_sessions() == []
        assert all(s.ended_at is not None and s.ended_at <= NOW for s in sessions)
        first = logical_date(NOW, 4) - timedelta(days=8 * 7 - 1)
        assert all(logical_date(s.started_at, 4) >= first for s in sessions)
        outcomes = {str(s.outcome) for s in sessions}
        assert outcomes == {"completed", "ended_early", "abandoned"}
        assert len({s.project for s in sessions}) >= 4 and None in {s.project for s in sessions}
        assert len({s.started_at.hour for s in sessions}) >= 8
        assert any(s.note for s in sessions)
        today_sessions = [s for s in sessions if logical_date(s.started_at, 4) == date(2026, 10, 14)]
        assert all(s.started_at < NOW for s in today_sessions)
        checkins = repo.checkins_between(datetime(2026, 1, 1), datetime(2027, 1, 1))
        responses = {str(c.response) for c in checkins}
        assert responses == {"focused", "drifted", "missed"}
        assert any(c.reason for c in checkins) and any(str(c.response) == "drifted" and not c.reason for c in checkins)
        assert len(repo.tasks_for_date(date(2026, 10, 14))) == 4
        assert repo.unfinished_before(date(2026, 10, 14))
        assert repo.tasks_for_date(date(2026, 10, 15))
        assert len(repo.open_captures()) == 3
        habits = repo.habits()
        assert len(habits) == counts["habits"] == 6
        logs = repo.habit_logs_between(date(2026, 1, 1), date(2026, 12, 31))
        assert len(logs) == counts["habit_logs"] > 0
        assert all(log.date <= date(2026, 10, 14) for log in logs)
        assert repo.get_day_note(date(2026, 10, 13)) is not None
    finally:
        repo.close()


def test_seed_demo_is_deterministic(seed, tmp_path: Path) -> None:  # noqa: ANN001
    a, b = _repo(tmp_path / "a.db"), _repo(tmp_path / "b.db")
    try:
        seed.seed_demo(a, NOW, weeks=3, seed=11)
        seed.seed_demo(b, NOW, weeks=3, seed=11)

        def key(repo: Repository) -> list[tuple]:
            rows = repo.recent_sessions(limit=9999)
            return [(s.task, s.project, s.started_at, s.focused_seconds, str(s.outcome)) for s in rows]

        assert key(a) == key(b) and key(a)
    finally:
        a.close()
        b.close()


def test_seed_demo_options(seed, tmp_path: Path) -> None:  # noqa: ANN001
    repo = _repo(tmp_path / "c.db")
    try:
        counts = seed.seed_demo(repo, NOW, weeks=1, habits=False, tasks=False, captures=False)
        assert counts["habits"] == counts["tasks"] == counts["captures"] == 0
        assert repo.habits() == [] and repo.open_captures() == []
        first = date(2026, 10, 8)
        assert all(logical_date(s.started_at, 4) >= first for s in repo.recent_sessions(limit=999))
    finally:
        repo.close()


def test_cli_seeds_and_refuses_twice(seed, tmp_path: Path, qapp, capsys) -> None:  # noqa: ANN001
    data = tmp_path / "demo"
    assert seed.main(["--data-dir", str(data), "--now", "2026-10-14T15:30", "--weeks", "2"]) == 0
    assert "Seeded" in capsys.readouterr().out
    settings = json.loads((data / "settings.json").read_text(encoding="utf-8"))
    assert settings["onboarded"] is True
    repo = _repo(data / "madhura.db")
    n = len(repo.recent_sessions(limit=9999))
    repo.close()
    assert n > 0
    assert seed.main(["--data-dir", str(data), "--now", "2026-10-14T15:30"]) == 1
    assert "--force" in capsys.readouterr().err
    assert seed.main(["--data-dir", str(data), "--now", "2026-10-14T15:30", "--weeks", "1", "--force"]) == 0
