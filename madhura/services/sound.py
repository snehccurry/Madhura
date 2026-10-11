"""Notification sounds (spec §7.6): one chime, volume pre-scaled into a cached WAV.

No audio library is used: Windows plays via ``winsound``, macOS via ``afplay``, Linux via the first
of ``paplay``/``pw-play``/``aplay``. Playback never blocks and never raises.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
import wave
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Any

from madhura import paths
from madhura.log import warn_once

__all__ = ["SoundPlayer", "EVENTS", "PRIORITY", "COALESCE_SECONDS"]

log = logging.getLogger(__name__)

EVENTS = ("checkin", "complete", "break_over", "habit", "nudge", "test")
#: Higher wins when two sounds collide within COALESCE_SECONDS.
PRIORITY = {"test": 6, "complete": 5, "checkin": 4, "habit": 3, "break_over": 2, "nudge": 1}
COALESCE_SECONDS = 2.5
_SETTING_FOR = {
    "checkin": "sound_checkin",
    "complete": "sound_complete",
    "break_over": "sound_break_over",
    "habit": "sound_habit",
    "nudge": "sound_nudge",
}
_LINUX_PLAYERS = ("paplay", "pw-play", "aplay")


class SoundPlayer:
    """Plays the chime for app events, honoring settings, attention state and coalescing."""

    def __init__(
        self,
        settings_store: Any,
        cache_dir: Path | None = None,
        attention_getter: Callable[[], str] | None = None,
        source: Path | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings_store = settings_store
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._attention_getter = attention_getter
        self._source = Path(source) if source is not None else paths.asset("sounds/chime.wav")
        self._monotonic = monotonic
        self._last_at: float | None = None
        self._last_priority = 0
        self._linux_player: str | None = None
        self._linux_resolved = False
        self._warned: set[str] = set()

    # ------------------------------------------------------------------ config

    def set_attention_getter(self, getter: Callable[[], str] | None) -> None:
        """Provide ``ctx.attention.state`` after construction."""
        self._attention_getter = getter

    def _cfg(self) -> Any:
        return getattr(self._settings_store, "settings", self._settings_store)

    def _cache(self) -> Path:
        base = self._cache_dir if self._cache_dir is not None else paths.cache_dir()
        folder = base / "sounds"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ------------------------------------------------------------------ policy

    @staticmethod
    def environment_muted() -> bool:
        """True when MADHURA_NO_SOUND is set or Qt runs on the offscreen platform."""
        if paths.no_sound():
            return True
        try:
            from PySide6.QtGui import QGuiApplication

            if QGuiApplication.instance() is not None and QGuiApplication.platformName() == "offscreen":
                return True
        except ImportError:
            return True
        return False

    def should_play(self, event: str) -> bool:
        """Settings + attention gate for *event* (does not consider the environment or coalescing)."""
        if event not in EVENTS:
            log.warning("Unknown sound event %r", event)
            return False
        cfg = self._cfg()
        if int(getattr(cfg, "sound_volume", 0)) <= 0:
            return False
        if event == "test":
            return True
        if not bool(getattr(cfg, _SETTING_FOR[event], False)):
            return False
        if self._attention_getter is not None:
            try:
                if self._attention_getter() in ("quiet", "busy"):
                    return False
            except Exception:  # noqa: BLE001 - a broken getter must not break sounds
                log.debug("attention_getter failed", exc_info=True)
        return True

    def _coalesce(self, event: str) -> bool:
        """True when *event* may play now (and records it)."""
        now = self._monotonic()
        priority = PRIORITY[event]
        if event != "test" and self._last_at is not None and now - self._last_at < COALESCE_SECONDS:
            if priority <= self._last_priority:
                return False
        self._last_at = now
        self._last_priority = priority
        return True

    def play(self, event: str) -> bool:
        """Play the sound for *event*; returns True when playback was launched."""
        if not self.should_play(event) or self.environment_muted():
            return False
        if not self._coalesce(event):
            return False
        volume = int(getattr(self._cfg(), "sound_volume", 40))
        try:
            path = self.scaled_wav(volume)
        except (OSError, wave.Error, EOFError):
            log.exception("Could not prepare the chime")
            return False
        return self._launch(path)

    # ------------------------------------------------------------------ audio

    def scaled_wav(self, volume: int) -> Path:
        """Path of the chime pre-scaled by gain (volume/100)² (cached for the current volume only)."""
        volume = max(0, min(100, int(volume)))
        target = self._cache() / f"chime_v{volume}.wav"
        if target.exists() and target.stat().st_size > 44:
            return target
        gain = (volume / 100.0) ** 2
        with wave.open(str(self._source), "rb") as src:
            params = src.getparams()
            frames = src.readframes(params.nframes)
        if params.sampwidth != 2:
            shutil.copyfile(self._source, target)
            self._prune_cache(target)
            return target
        samples = array("h")
        samples.frombytes(frames[: len(frames) - len(frames) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        scaled = array("h", (int(s * gain) for s in samples))
        if sys.byteorder == "big":
            scaled.byteswap()
        tmp = target.with_name(target.name + ".tmp")
        with wave.open(str(tmp), "wb") as dst:
            dst.setnchannels(params.nchannels)
            dst.setsampwidth(2)
            dst.setframerate(params.framerate)
            dst.writeframes(scaled.tobytes())
        os.replace(tmp, target)
        self._prune_cache(target)
        return target

    @staticmethod
    def _prune_cache(keep: Path) -> None:
        """Delete the chimes cached for other volumes and stale temp files (≈0.5 MB each).

        A file the OS is still playing may refuse deletion (Windows); it is skipped and retried
        on the next write.
        """
        for item in keep.parent.glob("chime_v*"):
            if item == keep:
                continue
            try:
                item.unlink()
            except OSError:
                log.debug("Could not delete cached sound %s", item, exc_info=True)

    def _launch(self, path: Path) -> bool:
        try:
            if sys.platform == "win32":
                import winsound

                flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
                winsound.PlaySound(str(path), flags)
                return True
            from PySide6.QtCore import QProcess

            if sys.platform == "darwin":
                player: str | None = "afplay"
            else:
                player = self._find_linux_player()
                if player is None:
                    return False
            ok = QProcess.startDetached(player, [str(path)])
            if not (ok[0] if isinstance(ok, tuple) else ok):
                raise OSError(f"could not start {player}")
            return True
        except Exception:  # noqa: BLE001 - sound must never crash the app
            warn_once(log, self._warned, "launch", "Sound playback failed")
            return False

    def _find_linux_player(self) -> str | None:
        if not self._linux_resolved:
            self._linux_resolved = True
            for name in _LINUX_PLAYERS:
                found = shutil.which(name)
                if found:
                    self._linux_player = found
                    break
            else:
                log.warning("No audio player found (%s); sounds are off", ", ".join(_LINUX_PLAYERS))
        return self._linux_player
