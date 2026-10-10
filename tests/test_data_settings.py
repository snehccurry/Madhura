from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from madhura.data.settings import VALIDATORS, Settings, SettingsStore, is_valid, validate

DEFAULTS = {
    "theme": "dark",
    "accent": "indigo",
    "reduce_motion": False,
    "time_format": "system",
    "default_minutes": 60,
    "duration_presets": [15, 25, 45, 60, 90],
    "daily_goal_minutes": 120,
    "day_start_hour": 4,
    "after_start": "mini",
    "checkin_enabled": True,
    "checkin_interval_minutes": 7,
    "checkin_adaptive": True,
    "checkin_timeout_seconds": 60,
    "breaks_enabled": True,
    "break_minutes": 5,
    "auto_start_break": False,
    "nudge_mode": "gentle",
    "nudge_idle_minutes": 5,
    "work_days": 31,
    "work_start": "09:00",
    "work_end": "18:00",
    "pill_mode": "hidden",
    "pill_show_seconds": False,
    "sound_volume": 40,
    "sound_checkin": True,
    "sound_complete": True,
    "sound_break_over": True,
    "sound_habit": True,
    "sound_nudge": False,
    "close_to_tray": True,
    "start_minimized": False,
    "onboarded": False,
}


def test_defaults_match_spec() -> None:
    assert Settings().to_dict() == DEFAULTS
    assert {f.name for f in fields(Settings)} == set(VALIDATORS)
    for key, value in DEFAULTS.items():
        assert is_valid(key, value), key
    assert "launch_at_login" not in DEFAULTS


@pytest.mark.parametrize(
    ("key", "good", "bad"),
    [
        ("theme", ["system", "dark", "light"], ["Dark", "", None, 1]),
        ("accent", ["indigo", "teal", "rose", "amber", "violet"], ["red"]),
        ("reduce_motion", [True, False], [1, 0, "true", None]),
        ("time_format", ["system", "24h", "12h"], ["24"]),
        ("default_minutes", [5, 60, 240], [4, 241, 60.0, True, "60"]),
        ("duration_presets", [[25], [5, 240], [15, 25, 45, 60, 90, 120], (15, 25)],
         [[], [25, 15], [15, 15], [4], [241], [1, 2, 3, 4, 5, 6, 7], [15, 25.0], [True], "15", None]),
        ("daily_goal_minutes", [30, 120, 720], [0, 29, 45, 750, 120.0]),
        ("day_start_hour", [0, 4, 6], [-1, 7, True]),
        ("after_start", ["mini", "stay"], ["hide"]),
        ("checkin_interval_minutes", [5, 7, 10, 15, 20, 30], [6, 0, 7.0, True]),
        ("checkin_timeout_seconds", [30, 60, 120], [45, 60.0]),
        ("break_minutes", [1, 30], [0, 31]),
        ("nudge_mode", ["off", "gentle", "firm"], ["loud"]),
        ("nudge_idle_minutes", [1, 60], [0, 61]),
        ("work_days", [0, 31, 127], [-1, 128, False]),
        ("work_start", ["00:00", "09:30", "23:59"], ["9:30", "24:00", "12:60", "0930", None]),
        ("work_end", ["18:00"], ["18:00:00"]),
        ("pill_mode", ["hidden", "always", "never"], ["auto"]),
        ("sound_volume", [0, 40, 100], [-1, 101, 40.5, True]),
        ("onboarded", [True], [1]),
    ],
)
def test_validators(key: str, good: list, bad: list) -> None:
    for value in good:
        assert is_valid(key, value), (key, value)
    for value in bad:
        assert not is_valid(key, value), (key, value)


def test_bool_vs_int_strictness() -> None:
    # bool is an int subclass — must be rejected for int settings and vice versa
    assert not is_valid("break_minutes", True)
    assert not is_valid("checkin_enabled", 1)
    with pytest.raises(KeyError):
        is_valid("nope", 1)
    with pytest.raises(ValueError):
        validate("break_minutes", True)


def test_update_emits_changed_per_key_and_is_atomic(settings_store: SettingsStore, qtbot) -> None:
    seen: list[str] = []
    settings_store.changed.connect(seen.append)
    changed = settings_store.update(theme="light", break_minutes=10, accent="indigo")
    assert changed == ["theme", "break_minutes"]
    assert seen == ["theme", "break_minutes"]
    assert settings_store.settings.theme == "light"
    assert settings_store.get("break_minutes") == 10
    assert settings_store.break_minutes == 10  # attribute convenience
    with pytest.raises(AttributeError):
        _ = settings_store.not_a_setting

    seen.clear()
    with pytest.raises(KeyError):
        settings_store.update(theme="dark", unknown_key=1)
    with pytest.raises(ValueError):
        settings_store.update(theme="dark", break_minutes=0)
    assert settings_store.settings.theme == "light"  # nothing applied
    assert seen == []
    assert settings_store.update(theme="light") == []
    with pytest.raises(KeyError):
        settings_store.get("nope")


def test_list_values_are_copied(settings_store: SettingsStore) -> None:
    presets = settings_store.get("duration_presets")
    presets.append(999)
    assert settings_store.get("duration_presets") == [15, 25, 45, 60, 90]
    new = [10, 20]
    settings_store.update(duration_presets=new)
    new.append(30)
    assert settings_store.settings.duration_presets == [10, 20]
    settings_store.update(duration_presets=(10, 20))
    assert settings_store.settings.duration_presets == [10, 20]


def test_debounced_save_and_flush(settings_store: SettingsStore, qtbot) -> None:
    path = settings_store.path
    settings_store.update(theme="light")
    assert settings_store.dirty
    assert not path.exists()  # debounced
    qtbot.waitUntil(lambda: path.exists(), timeout=2000)
    assert not settings_store.dirty
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["theme"] == "light"
    settings_store.update(sound_volume=70)
    settings_store.flush()
    assert json.loads(path.read_text(encoding="utf-8"))["sound_volume"] == 70
    assert not (path.parent / "settings.json.tmp").exists()
    settings_store.flush()  # clean → no-op


def test_reload_roundtrip(settings_store: SettingsStore, qapp) -> None:
    settings_store.update(day_start_hour=2, work_start="08:15", duration_presets=[20, 40])
    settings_store.flush()
    again = SettingsStore(settings_store.path)
    assert again.settings.day_start_hour == 2
    assert again.settings.work_start == "08:15"
    assert again.settings.duration_presets == [20, 40]


def test_load_drops_unknown_and_invalid(tmp_path: Path, qapp) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schema_version": 1, "theme": "light", "break_minutes": 99, "launch_at_login": True, "x": 1}),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    assert store.settings.theme == "light"
    assert store.settings.break_minutes == 5
    assert not hasattr(store.settings, "launch_at_login")


@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b"\xff\xfe\x00garbage"])
def test_corrupt_file_renamed_to_bad(tmp_path: Path, qapp, content: bytes) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(content)
    store = SettingsStore(path)
    assert store.settings == Settings()
    assert not path.exists()
    assert (tmp_path / "settings.json.bad").read_bytes() == content


def test_missing_file_gives_defaults(tmp_path: Path, qapp) -> None:
    store = SettingsStore(tmp_path / "nope" / "settings.json")
    assert store.settings == Settings()
    store.update(onboarded=True)
    store.flush()
    assert (tmp_path / "nope" / "settings.json").exists()


def test_default_path_uses_data_dir(data_dir: Path, qapp) -> None:
    store = SettingsStore()
    assert store.path == data_dir / "settings.json"


def test_atomic_write_retries_permission_error(settings_store: SettingsStore, monkeypatch: pytest.MonkeyPatch) -> None:
    import madhura.data.settings as mod

    calls = {"n": 0}
    real_replace = mod.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked by antivirus")
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", flaky)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    settings_store.update(theme="light")
    settings_store.flush()
    assert calls["n"] == 3
    assert json.loads(settings_store.path.read_text(encoding="utf-8"))["theme"] == "light"


def test_reset_to_defaults_keeps_onboarded(settings_store: SettingsStore) -> None:
    settings_store.update(theme="light", onboarded=True, break_minutes=12)
    seen: list[str] = []
    settings_store.changed.connect(seen.append)
    settings_store.reset_to_defaults()
    assert settings_store.settings.theme == "dark"
    assert settings_store.settings.break_minutes == 5
    assert settings_store.settings.onboarded is True
    assert sorted(seen) == ["break_minutes", "theme"]
