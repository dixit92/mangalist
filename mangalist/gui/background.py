"""Run one blocking call off the UI thread and hand its result (or a readable error) back on it.

Used by the volumes dialogs for everything that touches the network or the database (nyaa search, sending,
the connection test, the download list). The thread object keeps itself alive until it has finished, so a dialog
may be closed while a call is in flight: it calls :meth:`BackgroundCall.abandon` and the result is dropped.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Set

from PySide6.QtCore import QMetaMethod, QObject, Qt, QThread, QTimer, Signal

from .downloads_backend import BackendError

_log = logging.getLogger(__name__)

_ACTIVE: Set["BackgroundCall"] = set()


def describe_error(exc: BaseException) -> str:
    """The text shown for a failure: a BackendError's own message, otherwise only the exception's type (an
    unexpected exception's text could carry a URL or a secret)."""
    if isinstance(exc, BackendError):
        return str(exc) or "the operation failed"
    return f"unexpected error ({type(exc).__name__})"


def _disconnect_all(obj: QObject, signals) -> None:
    """Disconnect every slot of *signals* - only those that have one: PySide6 6.12 warns ("Failed to disconnect (None)
    from signal ...") for a signal nobody listens to, e.g. a call started without an ``on_error``."""
    for signal in signals:
        try:
            if obj.isSignalConnected(QMetaMethod.fromSignal(signal)):
                signal.disconnect()
        except (RuntimeError, TypeError):
            pass


def release_on_ui_thread(thread: QThread, worker: QObject) -> None:
    """Destroy *worker* (a QObject moved to *thread*) on the UI thread once *thread* has finished - in place of
    ``thread.finished.connect(worker.deleteLater)``. That deletes the worker inside its own thread as the thread ends,
    where Qt's destructor holds a signal/slot lock while PySide waits for Python's interpreter lock; the UI thread,
    holding the interpreter lock and connecting any signal that shares that lock, then waits for it: both stop for
    good (the test suite's GUI-stage hang, 2026-10-16; a stress run of the old way deadlocked within 500 threads).
    Here the last reference goes on the UI thread (``finished`` is delivered there), and Python deletes the worker with
    the interpreter lock already held. Connect it before ``thread.deleteLater``."""
    holder = [worker]

    def release() -> None:
        holder.clear()

    thread.finished.connect(release)


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
        _disconnect_all(self, (self.succeeded, self.failed))

    def _cleanup(self) -> None:
        _ACTIVE.discard(self)
        self.deleteLater()


def start_call(fn: Callable[[], Any], on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[str], None]] = None,
               on_finished: Optional[Callable[[], None]] = None) -> BackgroundCall:
    """Start ``fn()`` in a thread; ``on_done(result)`` / ``on_error(message)``, then ``on_finished()``, run on the
    calling thread. Every callback is connected BEFORE the thread starts: a signal emitted before its slot is
    connected is lost, so a call that finished quickly would otherwise leave its caller busy for ever."""
    call = BackgroundCall(fn)
    if on_done is not None:
        call.succeeded.connect(on_done)
    if on_error is not None:
        call.failed.connect(on_error)
    if on_finished is not None:
        call.finished.connect(on_finished)
    call.start()
    return call


class ProcessCall(QObject):
    """A job of :mod:`mangalist.process_pool` (a subprocess), with :class:`BackgroundCall`'s interface: ``succeeded``
    / ``failed`` and then ``finished`` on the thread that started it, :meth:`abandon` to drop the result."""

    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    _arrived = Signal(object)           # from the pool's thread: the future, delivered on this object's thread

    IDLE_MS = 60_000                    # the pool is shut down after a minute without a job (its memory goes back)

    def __init__(self, future, on_done=None, on_error=None, on_finished=None):
        super().__init__()
        self.abandoned = False
        self._future = future
        _ACTIVE.add(self)
        if on_done is not None:
            self.succeeded.connect(on_done)
        if on_error is not None:
            self.failed.connect(on_error)
        if on_finished is not None:
            self.finished.connect(on_finished)
        self._arrived.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        future.add_done_callback(self._arrived.emit)        # last: a job done already is delivered too

    def _deliver(self, future) -> None:
        try:
            if not future.cancelled():
                exc = future.exception()
                if exc is not None:
                    if not isinstance(exc, BackendError):
                        _log.warning("subprocess call failed: %s", type(exc).__name__)
                    self.failed.emit(describe_error(exc))
                else:
                    self.succeeded.emit(future.result())
        finally:
            self.finished.emit()
            _ACTIVE.discard(self)
            QTimer.singleShot(self.IDLE_MS, _release_idle_pool)
            self.deleteLater()

    def abandon(self) -> None:
        """Drop the result (the subprocess finishes its job; :func:`mangalist.process_pool.shutdown` stops it)."""
        self.abandoned = True
        _disconnect_all(self, (self.succeeded, self.failed))
        self._future.cancel()

    def wait(self, _msecs: int = 0) -> bool:
        return True


def _release_idle_pool() -> None:
    from .. import process_pool

    process_pool.release_if_idle()


def start_process_call(fn: Callable[..., Any], args: tuple = (), on_done: Optional[Callable[[Any], None]] = None,
                       on_error: Optional[Callable[[str], None]] = None,
                       on_finished: Optional[Callable[[], None]] = None) -> ProcessCall:
    """Run the Qt-free ``fn(*args)`` in a subprocess (:func:`mangalist.process_pool.submit`); the callbacks as with
    :func:`start_call`, connected before the job can finish."""
    from .. import process_pool

    return ProcessCall(process_pool.submit(fn, *args), on_done, on_error, on_finished)


# The window computes the series' states with this runner off the UI thread; a test's synchronous stand-in has no such
# mark (main_window: the states are then computed on first use, as before).
start_call.threaded = True
