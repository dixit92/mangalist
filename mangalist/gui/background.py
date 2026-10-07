"""Run one blocking call off the UI thread and hand its result (or a readable error) back on it.

Used by the volumes dialogs for everything that touches the network or the database (nyaa search, sending,
the connection test, the download list). The thread object keeps itself alive until it has finished, so a dialog
may be closed while a call is in flight: it calls :meth:`BackgroundCall.abandon` and the result is dropped.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Set

from PySide6.QtCore import QThread, Signal

from .downloads_backend import BackendError

_log = logging.getLogger(__name__)

_ACTIVE: Set["BackgroundCall"] = set()


def describe_error(exc: BaseException) -> str:
    """The text shown for a failure: a BackendError's own message, otherwise only the exception's type (an
    unexpected exception's text could carry a URL or a secret)."""
    if isinstance(exc, BackendError):
        return str(exc) or "the operation failed"
    return f"unexpected error ({type(exc).__name__})"


class BackgroundCall(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self._fn = fn
        self.abandoned = False
        _ACTIVE.add(self)
        self.finished.connect(self._cleanup)

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - shown to the owner, never crashes the GUI
            if not isinstance(exc, BackendError):
                _log.warning("background call failed: %s", type(exc).__name__)
            self.failed.emit(describe_error(exc))
            return
        self.succeeded.emit(result)

    def abandon(self) -> None:
        """Drop the result: whoever started the call no longer listens."""
        self.abandoned = True
        for signal in (self.succeeded, self.failed):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def _cleanup(self) -> None:
        _ACTIVE.discard(self)
        self.deleteLater()


def start_call(fn: Callable[[], Any], on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[str], None]] = None) -> BackgroundCall:
    """Start ``fn()`` in a thread; ``on_done(result)`` / ``on_error(message)`` run on the calling thread."""
    call = BackgroundCall(fn)
    if on_done is not None:
        call.succeeded.connect(on_done)
    if on_error is not None:
        call.failed.connect(on_error)
    call.start()
    return call
