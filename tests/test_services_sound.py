from __future__ import annotations

import sys
import wave
from array import array
from pathlib import Path

import pytest

from madhura.services import sound as sound_mod
from madhura.services.sound import SoundPlayer


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def player(settings_store, tmp_path: Path):
    mono = Clock()
    p = SoundPlayer(settings_store, cache_dir=tmp_path / "cache", monotonic=mono)
    p.mono = mono  # type: ignore[attr-defined]
    return p


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch, player: SoundPlayer) -> list[Path]:
    calls: list[Path] = []
    monkeypatch.setattr(SoundPlayer, "environment_muted", staticmethod(lambda: False))
    monkeypatch.setattr(player, "_launch", lambda path: calls.append(path) or True)
    return calls


def test_environment_muted_in_tests() -> None:
    assert SoundPlayer.environment_muted()  # MADHURA_NO_SOUND=1 and offscreen


def test_play_is_noop_when_muted(player: SoundPlayer, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Path] = []
    monkeypatch.setattr(player, "_launch", lambda path: called.append(path) or True)
    assert player.play("complete") is False
    assert called == []


def test_should_play_respects_settings(player: SoundPlayer, settings_store) -> None:
    assert player.should_play("checkin")
    assert player.should_play("complete")
    assert not player.should_play("nudge")  # sound_nudge defaults off
    assert not player.should_play("bogus")
    settings_store.update(sound_checkin=False)
    assert not player.should_play("checkin")
    assert player.should_play("test")
    settings_store.update(sound_volume=0)
    assert not player.should_play("test")


def test_attention_gate(player: SoundPlayer) -> None:
    state = {"value": "available"}
    player.set_attention_getter(lambda: state["value"])
    assert player.should_play("checkin")
    for value in ("quiet", "busy"):
        state["value"] = value
        assert not player.should_play("checkin")
        assert player.should_play("test")
    state["value"] = "away"
    assert player.should_play("checkin")

    def broken() -> str:
        raise RuntimeError

    player.set_attention_getter(broken)
    assert player.should_play("checkin")


def test_coalescing_by_priority(player: SoundPlayer, launched: list[Path], settings_store) -> None:
    settings_store.update(sound_nudge=True)
    assert player.play("checkin")
    player.mono.t += 1  # type: ignore[attr-defined]
    assert not player.play("habit")  # lower priority within 2.5 s
    assert not player.play("checkin")  # same priority
    assert player.play("complete")  # higher priority wins
    player.mono.t += 2.6  # type: ignore[attr-defined]
    assert player.play("nudge")  # window passed
    assert player.play("test")  # test always plays
    assert len(launched) == 4


def _peak(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2
        data = array("h")
        data.frombytes(w.readframes(w.getnframes()))
    return max(abs(x) for x in data)


def test_scaled_wav_cached_and_scaled(player: SoundPlayer, tmp_path: Path) -> None:
    full = player.scaled_wav(100)
    full_peak = _peak(full)
    half = player.scaled_wav(50)
    assert full.name == "chime_v100.wav" and half.name == "chime_v50.wav"
    assert full.parent == tmp_path / "cache" / "sounds"
    mtime = half.stat().st_mtime_ns
    assert player.scaled_wav(50) == half and half.stat().st_mtime_ns == mtime  # cached

    with wave.open(str(sound_mod.paths.asset("sounds/chime.wav")), "rb") as src:
        nframes = src.getnframes()
        channels = src.getnchannels()
    with wave.open(str(half), "rb") as w:
        assert w.getnframes() == nframes and w.getnchannels() == channels
    assert _peak(half) == pytest.approx(full_peak * 0.25, rel=0.01)
    assert _peak(player.scaled_wav(0)) == 0


def test_scaled_wav_keeps_only_the_current_volume(player: SoundPlayer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One ≈0.5 MB file per volume ever used would pile up (up to 101 files)."""
    folder = tmp_path / "cache" / "sounds"
    for volume in (10, 20, 30):
        player.scaled_wav(volume)
    (folder / "chime_v55.wav.tmp").write_bytes(b"stale")  # left by an interrupted write
    (folder / "other.wav").write_bytes(b"not ours")
    current = player.scaled_wav(40)
    assert sorted(p.name for p in folder.iterdir()) == ["chime_v40.wav", "other.wav"]
    assert player.scaled_wav(40) == current  # still cached

    # Windows: a file still being played can't be deleted → skipped now, removed on a later write.
    real_unlink = Path.unlink

    def busy(self: Path, missing_ok: bool = False) -> None:
        if self.name == "chime_v40.wav":
            raise PermissionError(32, "in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    player.scaled_wav(60)
    assert sorted(p.name for p in folder.glob("chime_v*")) == ["chime_v40.wav", "chime_v60.wav"]
    monkeypatch.setattr(Path, "unlink", real_unlink)
    player.scaled_wav(70)
    assert sorted(p.name for p in folder.glob("chime_v*")) == ["chime_v70.wav"]


def test_playback_failure_is_logged_once_at_warning(player: SoundPlayer, caplog: pytest.LogCaptureFixture) -> None:
    """A broken player must leave a trace in madhura.log (INFO), but not one per chime."""
    import logging

    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux player path")
    caplog.set_level(logging.DEBUG, logger="madhura.services.sound")
    player._linux_resolved = True
    player._linux_player = "/nonexistent/madhura-test-player"
    assert player._launch(player.scaled_wav(40)) is False
    assert player._launch(player.scaled_wav(40)) is False
    records = [r for r in caplog.records if r.getMessage() == "Sound playback failed"]
    assert [r.levelno for r in records] == [logging.WARNING, logging.DEBUG]
    assert records[0].exc_info is not None


def test_play_launches_scaled_file(player: SoundPlayer, launched: list[Path], settings_store) -> None:
    settings_store.update(sound_volume=70)
    assert player.play("complete")
    assert launched[0].name == "chime_v70.wav"


def test_linux_player_lookup(player: SoundPlayer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sound_mod.shutil, "which", lambda name: "/usr/bin/pw-play" if name == "pw-play" else None)
    assert player._find_linux_player() == "/usr/bin/pw-play"
    monkeypatch.setattr(sound_mod.shutil, "which", lambda name: None)
    assert player._find_linux_player() == "/usr/bin/pw-play"  # cached


def test_launch_never_raises(player: SoundPlayer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(player, "_find_linux_player", lambda: None)
    if sound_mod.sys.platform.startswith("linux"):
        assert player._launch(tmp_path / "x.wav") is False
