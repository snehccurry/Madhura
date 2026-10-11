from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from madhura.services.attention import AttentionMonitor
from madhura.services.notifier import Notifier


class FakeTray:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible
        self.messages: list[tuple] = []

    def isVisible(self) -> bool:  # noqa: N802 - Qt naming
        return self.visible

    def showMessage(self, *args) -> None:  # noqa: N802
        self.messages.append(args)


class Wrapper:
    def __init__(self, tray: FakeTray) -> None:
        self.tray = tray


# ================================================================== notifier


def test_notifier_without_tray_logs(qapp) -> None:
    notifier = Notifier(lambda: None)
    assert notifier.notify("Break's over", "Plan your next block") is False
    assert notifier.sent == [("Break's over", "Plan your next block")]
    assert Notifier().notify("a", "b") is False


def test_notifier_uses_visible_tray(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "supportsMessages", staticmethod(lambda: True))
    tray = FakeTray()
    notifier = Notifier(lambda: tray)
    assert notifier.notify("Title", "Body", msecs=1234)
    assert tray.messages[0][0:2] == ("Title", "Body")
    assert tray.messages[0][3] == 1234
    tray.visible = False
    assert not notifier.notify("Title", "Body")
    wrapped = FakeTray()
    notifier.set_tray_getter(lambda: Wrapper(wrapped))
    assert notifier.notify("Wrapped", "ok")
    assert wrapped.messages


def test_notifier_history_is_bounded(qapp) -> None:
    notifier = Notifier(lambda: None)
    for i in range(60):
        notifier.notify(str(i), "")
    assert len(notifier.sent) == 50
    assert notifier.sent[-1] == ("59", "")


def test_notifier_survives_broken_getter(qapp) -> None:
    def boom():
        raise RuntimeError

    assert Notifier(boom).notify("x", "y") is False


# ================================================================== attention


@pytest.fixture
def sources():
    return {"system": "available", "idle": 0.0}


@pytest.fixture
def monitor(qapp, fake_clock, repo, sources):
    m = AttentionMonitor(
        fake_clock,
        repo,
        system_attention_fn=lambda: sources["system"],
        idle_fn=lambda: sources["idle"],
    )
    yield m
    m.stop()


def test_attention_states(monitor: AttentionMonitor, sources) -> None:
    seen: list[str] = []
    monitor.changed.connect(seen.append)
    assert monitor.state() == "available"
    sources["system"] = "busy"
    assert monitor.refresh() == "busy"
    sources["idle"] = 301
    assert monitor.refresh() == "away"
    sources["idle"] = 299
    sources["system"] = "away"
    assert monitor.refresh() == "away"
    sources["system"] = "available"
    assert monitor.refresh() == "available"
    monitor.refresh()
    assert seen == ["busy", "away", "available"]
    assert monitor.input_idle_seconds() == 299


def test_attention_none_idle_is_available(qapp, fake_clock, repo) -> None:
    m = AttentionMonitor(fake_clock, repo, system_attention_fn=lambda: "available", idle_fn=lambda: None)
    assert m.state() == "available"
    assert m.input_idle_seconds() is None
    m.stop()


def test_quiet_mode(monitor: AttentionMonitor, repo, fake_clock, sources) -> None:
    seen: list[str] = []
    monitor.changed.connect(seen.append)
    monitor.set_quiet(60)
    assert monitor.state() == "quiet"
    assert monitor.is_quiet()
    assert monitor.quiet_until == datetime(2026, 10, 14, 11, 0)
    assert repo.kv_get("quiet_until") == "2026-10-14T11:00:00"
    sources["system"] = "busy"
    assert monitor.refresh() == "quiet"  # quiet wins
    assert 50 <= monitor._timer.interval() <= 15_000
    fake_clock.advance(3600)
    assert monitor.quiet_until is None
    assert monitor.state() == "busy"  # expiry noticed without waiting for the timer
    assert monitor.refresh() == "busy"
    assert repo.kv_get("quiet_until") is None
    assert monitor._timer.interval() == 2000  # fast poll while busy
    monitor.set_quiet(30)
    monitor.clear_quiet()
    assert monitor.state() == "busy"
    assert seen == ["quiet", "busy", "quiet", "busy"]


def test_quiet_persists_across_instances(qapp, fake_clock, repo) -> None:
    first = AttentionMonitor(fake_clock, repo, system_attention_fn=lambda: "available", idle_fn=lambda: 0.0)
    first.set_quiet(15)
    first.stop()
    second = AttentionMonitor(fake_clock, repo, system_attention_fn=lambda: "available", idle_fn=lambda: 0.0)
    assert second.state() == "quiet"
    assert second.quiet_until == datetime(2026, 10, 14, 10, 15)
    second.stop()
    repo.kv_set("quiet_until", 12345)  # garbage
    third = AttentionMonitor(fake_clock, repo, system_attention_fn=lambda: "available", idle_fn=lambda: 0.0)
    assert third.state() == "available"
    third.stop()


def test_attention_survives_failing_sources(qapp, fake_clock, repo) -> None:
    def boom():
        raise OSError("nope")

    m = AttentionMonitor(fake_clock, repo, system_attention_fn=boom, idle_fn=boom)
    assert m.state() == "available"
    assert m.system_state() == "available"
    m.stop()


def test_attention_default_sources_and_polling(qtbot, fake_clock, repo) -> None:
    m = AttentionMonitor(fake_clock, repo)
    assert m.state() == "available"  # Linux: no idle API, system "available"
    assert m._timer.isActive() and m._timer.interval() == 15_000
    m.stop()
    assert not m._timer.isActive()
    quiet_end = fake_clock.now() + timedelta(seconds=1)
    assert quiet_end > fake_clock.now()


def test_attention_probe_failures_warn_once(qapp, fake_clock, repo, caplog: pytest.LogCaptureFixture) -> None:
    """A probe that keeps failing switches 'away'/'busy' off: the first failure must reach the log
    (INFO level) with its traceback; repeats (every poll) stay at DEBUG."""
    import logging

    def broken() -> object:
        raise OSError("probe exploded")

    caplog.set_level(logging.DEBUG, logger="madhura.services.attention")
    m = AttentionMonitor(fake_clock, repo, system_attention_fn=broken, idle_fn=broken, autostart=False)  # type: ignore[arg-type]
    try:
        assert m.state() == "available"
        m.refresh()
        m.refresh()
    finally:
        m.stop()
    levels: dict[str, list[int]] = {}
    for r in caplog.records:
        levels.setdefault(r.getMessage(), []).append(r.levelno)
        if r.levelno == logging.WARNING:
            assert r.exc_info is not None
    assert levels["System attention probe failed"] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    assert levels["Input idle probe failed"] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
