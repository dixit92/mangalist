"""The subprocess pool's bookkeeping (:mod:`mangalist.process_pool`) never waits for itself: a job that is done before
its callback is attached (a quick job the pool's thread finished meanwhile, or a pool that broke) has the callback run
at once in the submitting thread - it used to take the pool's lock while the submit still held it, and the window's UI
thread (or a test) waited for ever (the test suite's intermittent hang, 2026-10-15/16)."""

from __future__ import annotations

import threading
from concurrent.futures import Future

from mangalist import process_pool


class _DoneAtOnce:
    """A pool whose jobs are finished by the time ``submit`` returns."""

    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_a_job_done_before_its_callback_does_not_deadlock_the_submit(monkeypatch):
    monkeypatch.setenv(process_pool.ENV_SUBPROCESS, "1")
    monkeypatch.setattr(process_pool, "_broken", False)
    monkeypatch.setattr(process_pool, "_pool", _DoneAtOnce())
    got = []
    worker = threading.Thread(target=lambda: got.append(process_pool.submit(sum, [1, 2]).result()), daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "submit waited for its own lock"
    assert got == [3] and not process_pool.busy()
