"""Logging setup and the global exception hook (spec §7.9)."""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys
import time
import traceback
from collections.abc import Callable
from types import TracebackType
from typing import IO

from madhura import paths

__all__ = ["setup_logging", "install_excepthook", "LOG_FORMAT"]

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_HANDLER_TAG = "_madhura_handler"
_DIALOG_INTERVAL_S = 30.0

log = logging.getLogger("madhura")


class _State:
    """Process-wide logging state (kept on a class to avoid import-time side effects)."""

    crash_file: IO[str] | None = None
    last_dialog: float | None = None
    showing_dialog: bool = False
    in_hook: bool = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Attach a rotating file handler (and stderr when available); enable faulthandler.

    Idempotent: calling it twice does not duplicate handlers. Returns the root logger.
    """
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT)

    have = {getattr(h, _HANDLER_TAG, None) for h in root.handlers}
    if "file" not in have:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                paths.log_dir() / "madhura.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setFormatter(formatter)
            setattr(file_handler, _HANDLER_TAG, "file")
            root.addHandler(file_handler)
        except OSError:
            # Logging must never stop the app from starting.
            pass
    if "stderr" not in have and sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        setattr(stream_handler, _HANDLER_TAG, "stderr")
        root.addHandler(stream_handler)

    if _State.crash_file is None:
        try:
            _State.crash_file = open(paths.log_dir() / "crash.log", "a", encoding="utf-8")
            faulthandler.enable(file=_State.crash_file)
        except (OSError, RuntimeError, ValueError):
            _State.crash_file = None
    return root


def _format_exc(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


def _schedule_dialog(show_dialog_cb: Callable[[str], None], message: str) -> None:
    """Show the friendly error at most once per 30 s, outside the current call stack."""
    now = time.monotonic()
    if _State.showing_dialog:
        return
    if _State.last_dialog is not None and now - _State.last_dialog < _DIALOG_INTERVAL_S:
        return
    try:
        from PySide6.QtCore import QCoreApplication, QTimer
    except ImportError:
        return
    if QCoreApplication.instance() is None:
        return
    _State.last_dialog = now

    def _show() -> None:
        if _State.showing_dialog:
            return
        _State.showing_dialog = True
        try:
            show_dialog_cb(message)
        except Exception:  # noqa: BLE001 - the error dialog itself must never recurse
            log.exception("Error dialog callback failed")
        finally:
            _State.showing_dialog = False

    QTimer.singleShot(0, _show)


def install_excepthook(show_dialog_cb: Callable[[str], None] | None = None) -> None:
    """Install ``sys.excepthook``: always log; optionally show a non-modal friendly error.

    *show_dialog_cb* receives a short message and is invoked via ``QTimer.singleShot(0)``, at most
    once per 30 s, never re-entrantly (so a failing paintEvent cannot loop dialogs).
    KeyboardInterrupt is passed to the default hook.
    """

    def _hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        if _State.in_hook:
            return
        _State.in_hook = True
        try:
            log.critical("Unhandled exception:\n%s", _format_exc(exc_type, exc, tb))
            if show_dialog_cb is not None:
                message = f"{exc_type.__name__}: {exc}" if str(exc) else exc_type.__name__
                _schedule_dialog(show_dialog_cb, message)
        except Exception:  # noqa: BLE001 - an excepthook must never raise
            pass
        finally:
            _State.in_hook = False

    sys.excepthook = _hook
