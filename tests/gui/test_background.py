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
