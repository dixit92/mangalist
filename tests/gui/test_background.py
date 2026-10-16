"""The background-call helper: results come back on the UI thread, errors become readable text (never an unexpected
exception's own message), and an abandoned call delivers nothing."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.gui.background import _ACTIVE, describe_error, start_call  # noqa: E402
from mangalist.gui.downloads_backend import BackendError  # noqa: E402

from .conftest import qapp, wait_until  # noqa: E402,F401


def test_the_call_runs_in_another_thread_and_the_result_arrives_on_the_ui_thread(qapp):
    seen = {}
    got = []

    def work():
        seen["worker"] = threading.get_ident()
        return 42

    call = start_call(work, lambda r: got.append((r, threading.get_ident())))
    wait_until(qapp, lambda: got and call not in _ACTIVE)
    assert got == [(42, threading.get_ident())] and seen["worker"] != threading.get_ident()


def test_errors_are_described_without_leaking_unexpected_messages(qapp):
    assert describe_error(BackendError("qBittorrent is not reachable")) == "qBittorrent is not reachable"
    assert describe_error(BackendError()) == "the operation failed"
    assert describe_error(ValueError("http://u:p@h/")) == "unexpected error (ValueError)"
    errors = []

    def boom():
        raise OSError("token=abc123")

    call = start_call(boom, None, errors.append)
    wait_until(qapp, lambda: errors and call not in _ACTIVE)
    assert errors == ["unexpected error (OSError)"]


def test_an_abandoned_call_delivers_nothing_and_cleans_up(qapp):
    release = threading.Event()
    got = []
    call = start_call(lambda: release.wait(10) or "late", got.append, got.append)
    call.abandon()
    release.set()
    wait_until(qapp, lambda: call not in _ACTIVE)
    assert got == [] and call.abandoned


def test_callbacks_are_connected_before_the_thread_starts(qapp, monkeypatch):
    # A call that finishes before its caller could connect would be lost (Qt drops a signal emitted before its slot
    # is connected). Run the thread body synchronously inside start(): every callback must still arrive.
    from mangalist.gui import background

    def start_now(self):
        self.run()
        self.finished.emit()
    monkeypatch.setattr(background.BackgroundCall, "start", start_now)
    got = []
    background.start_call(lambda: 7, got.append, None, lambda: got.append("finished"))
    background.start_call(lambda: 1 / 0, None, got.append, lambda: got.append("finished"))
    qapp.processEvents()
    assert got == [7, "finished", "unexpected error (ZeroDivisionError)", "finished"]


def test_a_threads_worker_is_destroyed_on_the_ui_thread_after_the_thread_ends(qapp):
    # thread.finished -> worker.deleteLater destroyed the worker inside its ending thread, where Qt holds a signal/slot
    # lock while PySide waits for the interpreter lock - the UI thread connecting a signal then deadlocked with it.
    from PySide6.QtCore import QObject, Qt, QThread, Signal

    from mangalist.gui.background import release_on_ui_thread

    class Worker(QObject):
        done = Signal()

        def run(self):
            self.done.emit()

    destroyed_on = []
    thread = QThread()
    worker = Worker()
    worker.moveToThread(thread)
    worker.destroyed.connect(lambda *_: destroyed_on.append(threading.current_thread()),
                             Qt.ConnectionType.DirectConnection)
    thread.started.connect(worker.run)
    worker.done.connect(thread.quit)
    release_on_ui_thread(thread, worker)
    del worker                                  # the window drops its own reference too
    thread.start()
    wait_until(qapp, lambda: destroyed_on and thread.isFinished())     # (thread.quit is delivered on the UI thread)
    assert thread.wait(5_000)
    assert destroyed_on == [threading.main_thread()]                    # not inside the thread as it ended
