"""Single-instance guard (spec §7.5): QLockFile is the authority, QLocalServer carries "show".

Usage (app.py)::

    guard = SingleInstance()
    status = guard.acquire()          # may run before QApplication exists
    if status is not InstanceStatus.PRIMARY:
        return guard.exit_code(status)   # 0 = existing instance was asked to show, 1 = error
    app = QApplication(argv)
    guard.listen()                    # needs the QApplication (socket notifiers)
    guard.show_requested.connect(lambda: ctx.window.show_main(activate=True))
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from madhura import paths
from madhura.core.models import ValueEnum

__all__ = ["SingleInstance", "InstanceStatus", "server_name_for"]

log = logging.getLogger(__name__)

_CONNECT_MS = 500
_IO_MS = 500
_LOCK_WAIT_MS = 100
#: The primary takes the lock before QApplication exists and listens only after it is built; a
#: launch racing that startup (autostart vs. a click at login) keeps trying this long.
#: Any connection error is retried until then: a pipe that isn't listening yet reports
#: ServerNotFound/ConnectionRefused on Unix but can surface as other errors on Windows.
_CONNECT_DEADLINE_MS = 2500
_CONNECT_RETRY_MS = 100


class InstanceStatus(ValueEnum):
    """Result of :meth:`SingleInstance.acquire`."""

    PRIMARY = "primary"  # we own the lock (or the guard is disabled)
    SIGNALED = "signaled"  # another instance is running and was asked to show itself
    FAILED = "failed"  # another instance holds the lock but could not be reached

    def __bool__(self) -> bool:
        """Truthy only for PRIMARY, so ``if guard.acquire():`` reads naturally."""
        return self is InstanceStatus.PRIMARY


def server_name_for(data_dir: Path) -> str:
    """``madhura-`` + sha1(f"{user}|{resolved data dir}")[:16]."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - getuser can fail in odd environments
        user = "user"
    digest = hashlib.sha1(f"{user}|{Path(data_dir).resolve()}".encode()).hexdigest()[:16]
    return f"madhura-{digest}"


class SingleInstance(QObject):
    """Ensures one Madhura per user + data dir and forwards "show" requests to the primary."""

    show_requested = Signal()

    def __init__(self, data_dir: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._data_dir = Path(data_dir) if data_dir is not None else paths.data_dir()
        self._lock: QLockFile | None = None
        self._server: QLocalServer | None = None
        self._clients: list[QLocalSocket] = []
        self._buffers: dict[int, bytes] = {}
        self.disabled = paths.no_single_instance()
        self.error: str | None = None
        self._last_failure: str | None = None

    @property
    def server_name(self) -> str:
        """IPC endpoint name."""
        return server_name_for(self._data_dir)

    @property
    def lock_path(self) -> Path:
        """Lock file location."""
        return self._data_dir / "madhura.lock"

    @property
    def is_primary(self) -> bool:
        """True after a successful acquire()."""
        return self.disabled or (self._lock is not None and self._lock.isLocked())

    @property
    def is_listening(self) -> bool:
        """True while the IPC server is listening."""
        return self._server is not None and self._server.isListening()

    @staticmethod
    def exit_code(status: InstanceStatus) -> int:
        """Process exit code for a non-primary status (SIGNALED → 0, FAILED → 1)."""
        return 1 if status is InstanceStatus.FAILED else 0

    # ------------------------------------------------------------------ acquire

    def acquire(self) -> InstanceStatus:
        """Take the lock, or signal the running instance. Starts listening if a Qt app exists."""
        if self.disabled:
            return InstanceStatus.PRIMARY
        self._data_dir.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.lock_path))
        lock.setStaleLockTime(0)
        if lock.tryLock(_LOCK_WAIT_MS):
            self._lock = lock
            if QCoreApplication.instance() is not None:
                self.listen()
            return InstanceStatus.PRIMARY
        if self.signal_existing():
            return InstanceStatus.SIGNALED
        self.error = "Madhura is already running but did not respond. Close it and try again."
        log.error(
            "Lock held by another instance that did not answer on %s (%s)",
            self.server_name,
            self._last_failure or "no details",
        )
        return InstanceStatus.FAILED

    def _connect(self) -> QLocalSocket | None:
        """Connect to the primary, retrying while it is still starting up (lock taken, not yet
        listening) until :data:`_CONNECT_DEADLINE_MS`."""
        deadline = time.monotonic() + _CONNECT_DEADLINE_MS / 1000.0
        while True:
            sock = QLocalSocket()
            sock.connectToServer(self.server_name)
            if sock.waitForConnected(_CONNECT_MS):
                return sock
            self._last_failure = f"connect: {sock.error().name}: {sock.errorString()}"
            sock.abort()
            if time.monotonic() >= deadline:
                log.debug("Could not reach the running instance: %s", self._last_failure)
                return None
            time.sleep(_CONNECT_RETRY_MS / 1000.0)

    def signal_existing(self) -> bool:
        """Connect to the primary, read ``pid:<n>``, allow it to take focus, send ``show``."""
        sock = self._connect()
        if sock is None:
            return False
        try:
            pid = None
            if sock.waitForReadyRead(_IO_MS) or sock.canReadLine():
                line = bytes(sock.readLine()).strip()
                if line.startswith(b"pid:"):
                    try:
                        pid = int(line[4:])
                    except ValueError:
                        pid = None
            if pid is not None:
                from madhura.platform.windows import allow_set_foreground

                allow_set_foreground(pid)
            sock.write(b"show\n")
            sock.flush()
            ok = sock.waitForBytesWritten(_IO_MS) or sock.bytesToWrite() == 0
            if not ok:
                self._last_failure = f"write: {sock.error().name}: {sock.errorString()}"
            return bool(ok)
        finally:
            sock.disconnectFromServer()
            if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                sock.waitForDisconnected(_IO_MS)

    # ------------------------------------------------------------------ server

    def listen(self) -> bool:
        """Start the IPC server (requires a QCoreApplication). Idempotent."""
        if self.disabled or self._server is not None:
            return self.disabled or self.is_listening
        if QCoreApplication.instance() is None:
            log.warning("SingleInstance.listen() called before QApplication; ignored")
            return False
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.server_name):
            log.warning("IPC server failed to listen: %s", server.errorString())
            server.deleteLater()
            return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                break
            self._clients.append(sock)
            self._buffers[id(sock)] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))
            # A launch that raced our startup (listening, event loop not running yet) has already
            # written "show" and hung up: read it before the pid write fails and drops the socket.
            if sock.waitForReadyRead(0) or sock.bytesAvailable():
                self._on_ready_read(sock)
            if sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
                sock.write(f"pid:{os.getpid()}\n".encode())
                sock.flush()

    def _on_ready_read(self, sock: QLocalSocket) -> None:
        data = self._buffers.get(id(sock), b"") + bytes(sock.readAll())
        while b"\n" in data:
            line, data = data.split(b"\n", 1)
            if line.strip() == b"show":
                log.info("Another launch asked us to show the window")
                self.show_requested.emit()
        if sock in self._clients:
            self._buffers[id(sock)] = data[-256:]

    def _drop(self, sock: QLocalSocket) -> None:
        if sock in self._clients:
            self._clients.remove(sock)
        self._buffers.pop(id(sock), None)
        sock.deleteLater()

    # ------------------------------------------------------------------ release

    def release(self) -> None:
        """Stop listening and unlock (on quit)."""
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None
