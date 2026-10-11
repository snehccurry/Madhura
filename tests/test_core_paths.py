from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from madhura import paths


def test_data_dir_uses_env_and_creates_dirs(data_dir: Path) -> None:
    assert paths.data_dir() == data_dir
    assert paths.db_path() == data_dir / "madhura.db"
    assert paths.settings_path() == data_dir / "settings.json"
    for fn, name in ((paths.log_dir, "logs"), (paths.cache_dir, "cache"), (paths.backups_dir, "backups")):
        folder = fn()
        assert folder == data_dir / name
        assert folder.is_dir()


def test_data_dir_created_lazily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "nested" / "dir"
    monkeypatch.setenv("MADHURA_DATA_DIR", str(target))
    assert not target.exists()
    assert paths.data_dir() == target
    assert target.is_dir()


def test_default_data_dir_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux layout")
    monkeypatch.delenv("MADHURA_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir() == tmp_path / "xdg" / "madhura"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/ignored")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert paths.data_dir() == tmp_path / "home" / ".local" / "share" / "madhura"


def test_env_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MADHURA_X", "1")
    assert paths.env_flag("MADHURA_X")
    for value in ("true", "YES", "on"):
        monkeypatch.setenv("MADHURA_X", value)
        assert paths.env_flag("MADHURA_X")
    for value in ("0", "false", "", "off"):
        monkeypatch.setenv("MADHURA_X", value)
        assert not paths.env_flag("MADHURA_X", default=True)
    monkeypatch.delenv("MADHURA_X")
    assert paths.env_flag("MADHURA_X", default=True)
    assert paths.no_sound()
    assert paths.no_single_instance()
    monkeypatch.delenv("MADHURA_FORCE_TRAY", raising=False)
    assert paths.force_tray() is None
    monkeypatch.setenv("MADHURA_FORCE_TRAY", "1")
    assert paths.force_tray() is True
    monkeypatch.setenv("MADHURA_FORCE_TRAY", "0")
    assert paths.force_tray() is False


def test_asset_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chime = paths.asset("sounds/chime.wav")
    assert chime == paths.repo_root() / "madhura" / "assets" / "sounds" / "chime.wav"
    assert chime.is_file()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.asset("icon.png") == tmp_path / "madhura" / "assets" / "icon.png"


def test_legacy_habits_candidates(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / ".hidden").mkdir()
    legacy = '{"_default": {"1": {"title": "x", "time": "12:00"}}}'
    (root / "habits.json").write_text(legacy, encoding="utf-8")
    (root / "a" / "b" / "habits.json").write_text(legacy, encoding="utf-8")
    (root / "a" / "b" / "c" / "habits.json").write_text(legacy, encoding="utf-8")  # too deep
    (root / ".hidden" / "habits.json").write_text(legacy, encoding="utf-8")
    (root / "a" / "habits.json").write_text('{"habits": []}', encoding="utf-8")  # not TinyDB
    found = paths.legacy_habits_candidates([root, root, tmp_path / "missing"])
    assert found == [root / "habits.json", root / "a" / "b" / "habits.json"]


def test_legacy_fixture_detected(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "legacy_habits.json"
    target = tmp_path / "old"
    target.mkdir()
    (target / "habits.json").write_bytes(fixture.read_bytes())
    assert paths.legacy_habits_candidates([target]) == [target / "habits.json"]
    # default roots never raise
    assert isinstance(paths.legacy_habits_candidates(), list)


def test_setup_logging_is_idempotent(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from madhura import log as mlog

    enabled: list[object] = []
    monkeypatch.setattr(mlog.faulthandler, "enable", lambda file=None, **kw: enabled.append(file))
    monkeypatch.setattr(mlog._State, "crash_file", None)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        mlog.setup_logging()
        count = len(root.handlers)
        mlog.setup_logging()
        assert len(root.handlers) == count
        assert len(enabled) == 1
        assert (data_dir / "logs" / "crash.log").exists()
        logging.getLogger("madhura.test").warning("hello log")
        for handler in root.handlers:
            handler.flush()
        assert (data_dir / "logs" / "madhura.log").read_text(encoding="utf-8").count("hello log") == 1
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        if mlog._State.crash_file is not None:
            mlog._State.crash_file.close()


def test_excepthook_logs_and_throttles_dialog(qapp, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    from madhura import log as mlog

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(mlog._State, "last_dialog", None)
    shown: list[str] = []
    mlog.install_excepthook(shown.append)
    hook = sys.excepthook
    for _ in range(3):
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            hook(type(exc), exc, exc.__traceback__)
    qtbot.waitUntil(lambda: len(shown) == 1, timeout=2000)
    qtbot.wait(20)
    assert shown == ["RuntimeError: boom"]


def test_excepthook_without_callback(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from madhura import log as mlog

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    mlog.install_excepthook(None)
    with caplog.at_level(logging.CRITICAL, logger="madhura"):
        try:
            raise ValueError("bad")
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
    assert "ValueError: bad" in caplog.text


def test_legacy_scan_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "huge"
    legacy = '{"_default": {}}'
    for i in range(30):
        (root / f"d{i:02d}").mkdir(parents=True)
    (root / "d29" / "habits.json").write_text(legacy, encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "habits.json").write_text(legacy, encoding="utf-8")
    assert paths.legacy_habits_candidates([root], max_dirs_per_root=10) == []
    assert paths.legacy_habits_candidates([root]) == [root / "d29" / "habits.json"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG user-dirs")
def test_legacy_habits_found_on_redirected_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Desktop may live elsewhere (OneDrive Known Folder Move on Windows, ~/Schreibtisch on a
    German Linux desktop): use the folder the OS reports, not only ~/Desktop."""
    home = tmp_path / "home"
    desktop = home / "Schreibtisch"
    (desktop / "Madhura-v1").mkdir(parents=True)
    legacy = desktop / "Madhura-v1" / "habits.json"
    legacy.write_text('{"_default": {"1": {"title": "x", "time": "12:00"}}}', encoding="utf-8")
    config = home / ".config"
    config.mkdir()
    (config / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.chdir(tmp_path)
    assert paths.legacy_habits_candidates() == [legacy]
