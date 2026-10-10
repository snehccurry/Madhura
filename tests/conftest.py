"""Shared pytest fixtures for Madhura.

Environment is forced headless and silent at import time (before any Qt import):
QT_QPA_PLATFORM=offscreen, MADHURA_NO_SINGLE_INSTANCE=1, MADHURA_NO_SOUND=1.
Every test gets its own MADHURA_DATA_DIR under tmp_path.
"""

from __future__ import annotations

import gc
import os
import sys

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


def pytest_collection_finish(session: pytest.Session) -> None:
    """Move everything alive after collection (modules, Qt bindings, test functions) out of the
    garbage collector's reach so the per-test ``gc.collect()`` below only scans test objects."""
    gc.collect()
    gc.freeze()


@pytest.fixture(autouse=True)
def _qt_hygiene() -> Iterator[None]:
    """Leave no Qt state behind: after every test (and after its own fixtures' teardown) drop the
    popup stack, hide open popups, delete every leftover parentless window (pills, panels, main
    windows), collect garbage at a safe point and run the resulting deferred deletes.

    Without this, windows from earlier tests pile up — each theme/QSS change re-polishes all of
    them (the suite slowed down quadratically) — and cyclic garbage holding Qt objects is
    collected at random moments, possibly inside a later test's signal emission.
    """
    yield
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    popup = sys.modules.get("madhura.ui.widgets.popup")
    if popup is not None:
        popup.PopupStack.reset()
    for w in QApplication.topLevelWidgets():
        # Only real windows. Popup-type windows (QCompleter popups, menus) are owned by their
        # completer/menu object even though they have no parent: deleting one here would leave
        # its owner with a dangling pointer that it deletes again later (segfault).
        if w.windowType() == Qt.WindowType.Popup:
            if w.isVisible():
                w.hide()  # an open popup would grab the mouse in the next tests
        elif w.parent() is None:
            w.hide()
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.freeze()  # survivors are long-lived (modules, caches): keep the next collection small
    theme = sys.modules.get("madhura.ui.theme")
    if theme is not None:
        theme.set_current(None)
    if app.styleSheet():
        app.setStyleSheet("")


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
