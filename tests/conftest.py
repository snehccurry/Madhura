"""Shared pytest fixtures for Madhura.

Environment is forced headless and silent at import time (before any Qt import):
QT_QPA_PLATFORM=offscreen, MADHURA_NO_SINGLE_INSTANCE=1, MADHURA_NO_SOUND=1.
Every test gets its own MADHURA_DATA_DIR under tmp_path.
"""

from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["MADHURA_NO_SINGLE_INSTANCE"] = "1"
os.environ["MADHURA_NO_SOUND"] = "1"

from collections.abc import Iterator  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

import pytest  # noqa: E402

from madhura.core.clock import FakeClock  # noqa: E402

if TYPE_CHECKING:
    from madhura.data.repository import Repository
    from madhura.data.settings import SettingsStore
    from madhura.services.engine import FocusEngine

#: Wednesday 2026-10-14 10:00 local — the canonical "now" for tests.
FAKE_NOW = datetime(2026, 10, 14, 10, 0, 0)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point MADHURA_DATA_DIR at a fresh temp directory for every test."""
    data = tmp_path / "madhura-data"
    data.mkdir()
    monkeypatch.setenv("MADHURA_DATA_DIR", str(data))
    return data


@pytest.fixture
def data_dir(_isolated_data_dir: Path) -> Path:
    """The per-test data directory (same as MADHURA_DATA_DIR)."""
    return _isolated_data_dir


@pytest.fixture
def fake_clock() -> FakeClock:
    """FakeClock at Wednesday 2026-10-14 10:00 (naive, no DST)."""
    return FakeClock(FAKE_NOW)


@pytest.fixture
def repo(fake_clock: FakeClock) -> Iterator[Repository]:
    """Repository on a temp database file, using the fake clock."""
    from madhura import paths
    from madhura.data.repository import Repository

    repository = Repository(paths.db_path(), clock=fake_clock)
    yield repository
    repository.close()


@pytest.fixture
def settings_store(qapp) -> Iterator[SettingsStore]:
    """SettingsStore on a temp settings.json (flushed at teardown)."""
    from madhura import paths
    from madhura.data.settings import SettingsStore

    store = SettingsStore(paths.settings_path())
    yield store
    store.flush()


@pytest.fixture
def engine(qapp, repo, settings_store, fake_clock) -> Iterator[FocusEngine]:
    """FocusEngine wired to the fake clock, temp repo and settings (its timer is stopped at teardown)."""
    from madhura.services.engine import FocusEngine

    focus_engine = FocusEngine(repo, settings_store, fake_clock)
    yield focus_engine
    focus_engine.stop_timer()
