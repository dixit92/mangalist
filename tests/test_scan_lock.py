"""The cross-process scan lock (the GUI and the headless runner never scan or record at once; a waiting side waits)
and the retry on ``database is locked``."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from mangalist.scan_lock import is_locked_error, retry_if_locked, scan_lock

ROOT = Path(__file__).resolve().parents[1]


def _holder(lock: Path, seconds: float, ready: Path) -> subprocess.Popen:
    code = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from mangalist.scan_lock import scan_lock
        with scan_lock(path=Path({str(lock)!r})):
            Path({str(ready)!r}).write_text("held")
            time.sleep({seconds})
    """)
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.Popen([sys.executable, "-c", code], env=env)
    deadline = time.monotonic() + 30
    while not ready.exists():
        assert proc.poll() is None, "the holder process ended early"
        assert time.monotonic() < deadline, "the holder never took the lock"
        time.sleep(0.02)
    return proc


def test_a_second_process_waits_for_the_lock(tmp_path):
    lock = tmp_path / "scan.lock"
    proc = _holder(lock, 1.0, tmp_path / "ready")
    told = []
    started = time.monotonic()
    try:
        with scan_lock(path=lock, on_wait=lambda: told.append(1), poll=0.02) as held:
            waited = time.monotonic() - started
    finally:
        proc.wait(30)
    assert held and told == [1] and waited >= 0.5


def test_the_wait_runs_out_and_the_scan_goes_ahead(tmp_path):
    lock = tmp_path / "scan.lock"
    proc = _holder(lock, 3.0, tmp_path / "ready")
    try:
        with scan_lock(path=lock, wait=0.2, poll=0.02) as held:
            assert held is False
    finally:
        proc.wait(30)


def test_the_lock_is_reentrant_on_one_thread_and_shared_between_threads(tmp_path):
    lock = tmp_path / "scan.lock"
    order = []
    with scan_lock(path=lock) as outer:
        with scan_lock(path=lock, wait=1) as inner:          # a nested scan does not wait for itself
            assert outer and inner

        def other():
            with scan_lock(path=lock):
                order.append("thread")

        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.2)
        order.append("main")
    t.join(10)
    assert order == ["main", "thread"]                        # the other thread waited for the outer block


def test_without_a_database_file_there_is_nothing_to_lock():
    with scan_lock(None) as held:
        assert held


def test_database_is_locked_is_tried_again():
    tries, pauses = [], []

    def flaky():
        tries.append(1)
        if len(tries) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "recorded"

    assert retry_if_locked(flaky, what="Recording", sleep=pauses.append) == "recorded"
    assert pauses == [2.0, 4.0]
    with pytest.raises(sqlite3.OperationalError):
        retry_if_locked(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")), what="x",
                        tries=2, sleep=pauses.append)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):     # other errors are not retried
        retry_if_locked(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("no such table: x")), what="x",
                        sleep=lambda s: pytest.fail("retried"))
    assert is_locked_error(sqlite3.OperationalError("database table is locked"))
    assert not is_locked_error(ValueError("locked"))


def test_a_wait_ends_when_the_scan_is_stopped(tmp_path):
    lock = tmp_path / "scan.lock"
    proc = _holder(lock, 3.0, tmp_path / "ready")

    class Stopped(Exception):
        pass

    ticks = []

    def tick():
        ticks.append(1)
        if len(ticks) >= 3:
            raise Stopped()                     # the window closes / the runner shuts down while it waits

    try:
        with pytest.raises(Stopped):
            with scan_lock(path=lock, tick=tick, poll=0.02):
                pytest.fail("the block must not run")
        with scan_lock(path=lock, wait=0.1, poll=0.02) as held:      # nothing left behind in this process
            assert held is False
    finally:
        proc.wait(30)
