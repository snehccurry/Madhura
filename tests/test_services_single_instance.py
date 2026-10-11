from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from madhura.services.single_instance import InstanceStatus, SingleInstance, server_name_for

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MADHURA_NO_SINGLE_INSTANCE", raising=False)


def test_server_name_is_stable_and_scoped(tmp_path: Path) -> None:
    a = server_name_for(tmp_path / "a")
    assert a.startswith("madhura-") and len(a) == len("madhura-") + 16
    assert a == server_name_for(tmp_path / "a")
    assert a != server_name_for(tmp_path / "b")


def test_disabled_by_env(qapp, data_dir: Path) -> None:
    guard = SingleInstance()
    assert guard.disabled
    assert guard.acquire() is InstanceStatus.PRIMARY
    assert guard.is_primary
    assert guard.listen() is True
    assert not (data_dir / "madhura.lock").exists()
    guard.release()


def test_primary_locks_and_listens(qapp, enabled, data_dir: Path) -> None:
    guard = SingleInstance()
    try:
        assert guard.acquire() is InstanceStatus.PRIMARY
        assert guard.is_primary
        assert guard.is_listening
        assert (data_dir / "madhura.lock").exists()
        assert guard.listen() is True  # idempotent
    finally:
        guard.release()
    assert not guard.is_listening


def test_exit_codes() -> None:
    assert SingleInstance.exit_code(InstanceStatus.SIGNALED) == 0
    assert SingleInstance.exit_code(InstanceStatus.FAILED) == 1
    assert str(InstanceStatus.PRIMARY) == "primary"
    assert InstanceStatus.PRIMARY
    assert not InstanceStatus.SIGNALED
    assert not InstanceStatus.FAILED
    assert InstanceStatus.FAILED == "failed"


def _second_instance(data_dir: Path) -> subprocess.Popen[str]:
    code = textwrap.dedent(
        """
        import os, sys
        os.environ.pop("MADHURA_NO_SINGLE_INSTANCE", None)
        from madhura.services.single_instance import SingleInstance
        guard = SingleInstance()
        status = guard.acquire()
        print(status.value)
        sys.exit(guard.exit_code(status))
        """
    )
    env = {**os.environ, "MADHURA_DATA_DIR": str(data_dir), "PYTHONPATH": str(REPO_ROOT)}
    env.pop("MADHURA_NO_SINGLE_INSTANCE", None)
    return subprocess.Popen(
        [sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def test_second_instance_signals_primary(qtbot, enabled, data_dir: Path) -> None:
    guard = SingleInstance()
    shows: list[bool] = []
    guard.show_requested.connect(lambda: shows.append(True))
    try:
        assert guard.acquire() is InstanceStatus.PRIMARY
        proc = _second_instance(data_dir)
        qtbot.waitUntil(lambda: proc.poll() is not None and bool(shows), timeout=15_000)
        out, err = proc.communicate(timeout=5)
        assert proc.returncode == 0, err
        assert out.strip() == "signaled"
        assert shows == [True]
    finally:
        guard.release()


def test_lock_held_but_no_server_fails(qapp, enabled, data_dir: Path) -> None:
    holder = SingleInstance()
    try:
        assert holder.acquire() is InstanceStatus.PRIMARY
        holder._server.close()  # simulate a hung primary: lock held, nobody answering
        proc = _second_instance(data_dir)
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 1, err
        assert out.strip() == "failed"
    finally:
        holder.release()


def test_second_launch_waits_for_a_starting_primary(qtbot, enabled, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The primary holds the lock but is not listening yet (still building QApplication): a racing
    launch must keep trying instead of reporting "did not respond"."""
    from madhura.services import single_instance as si

    holder = SingleInstance()
    shows: list[bool] = []
    holder.show_requested.connect(lambda: shows.append(True))
    try:
        assert holder.acquire() is InstanceStatus.PRIMARY
        holder._server.close()
        holder._server.deleteLater()
        holder._server = None  # lock taken, not listening yet
        waits: list[float] = []

        def primary_comes_up(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) == 2:
                assert holder.listen()

        monkeypatch.setattr(si.time, "sleep", primary_comes_up)
        second = SingleInstance()
        assert second.acquire() is InstanceStatus.SIGNALED
        assert len(waits) == 2
        qtbot.waitUntil(lambda: shows == [True], timeout=5_000)
    finally:
        holder.release()
