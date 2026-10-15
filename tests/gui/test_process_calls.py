"""A long count in a subprocess, from the window's side (UI performance stage 2): its result arrives on the UI thread
through the same callbacks as a background thread's; abandoned, it is dropped."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from mangalist import process_pool  # noqa: E402
from mangalist.gui.background import start_process_call  # noqa: E402

from .conftest import qapp, wait_until  # noqa: E402,F401


def test_a_subprocess_result_arrives_on_the_ui_thread(qapp, monkeypatch):
    monkeypatch.setenv(process_pool.ENV_SUBPROCESS, "1")
    got, threads, finished = [], [], []
    try:
        start_process_call(process_pool.count_by_folder, ([],),
                           lambda result: got.append(result) or threads.append(threading.current_thread()),
                           lambda message: got.append(message), lambda: finished.append(1))
        wait_until(qapp, lambda: finished, 120)
        assert got == [(0, {})] and threads == [threading.main_thread()]
        dropped = []
        call = start_process_call(process_pool.count_by_folder, ([],), dropped.append, dropped.append,
                                  lambda: finished.append(2))
        call.abandon()
        wait_until(qapp, lambda: 2 in finished or call._future.cancelled(), 120)
        qapp.processEvents()
        assert dropped == []
    finally:
        process_pool.shutdown()


def test_without_subprocesses_the_job_runs_here(qapp, monkeypatch):
    monkeypatch.setenv(process_pool.ENV_SUBPROCESS, "0")
    got = []
    start_process_call(process_pool.count_by_folder, ([],), got.append)
    wait_until(qapp, lambda: got, 5)
    assert got == [(0, {})]
