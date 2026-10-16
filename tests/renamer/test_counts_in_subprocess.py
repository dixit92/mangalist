"""The long counts the window runs in a subprocess (UI performance stage 2, :mod:`mangalist.process_pool`): the same
numbers as the window's own count of its scanned rows, read from the database alone - in this process (the fallback)
and in a real spawned subprocess. Made-up series in the test's temporary folder."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mangalist import process_pool
from mangalist.renamer import Renamer

from .conftest import fmd2, make_library, payload


@pytest.fixture
def scanned(db, library):
    make_library(db, library, {
        "Series A": {fmd2(1, "0001", "Start", "Group A"): payload("a1"), fmd2(2, "0002", "Next", "Group A"): payload("a2"),
                     "Ch. 0003.00 (Third) [Group A].cbz": payload("a3")},
        "Series B": {"Series B v01 (2020) (Digital) (Rel).cbz": payload("b1"),
                     "Series B v02 (2021) (Digital) (Rel).cbz": payload("b2")},
        "Series C": {"Series C v01.cbz": payload("c1"), "Series C v01 (Digital).cbz": payload("c1-other")},
    })
    from mangalist.scanner import scan_library

    entries = scan_library(db.list_roots(), db=db).entries
    return db, library, entries


def test_the_rename_count_from_the_records_is_the_windows_count(scanned):
    db, library, entries = scanned
    expected = Renamer(db, rescan=None).pending_counts(entries)        # what the window counted from its rows
    assert expected                                                    # the naming scheme renames some of these
    assert process_pool.rename_pending_counts(str(db.path)) == expected
    a = str(library / "Series A")
    assert process_pool.rename_pending_counts(str(db.path), [a]) == {k: v for k, v in expected.items() if k == a}


def test_the_duplicate_count_from_a_subprocess_job(scanned):
    from mangalist.duplicates import find_duplicate_files

    db, _library, _entries = scanned
    assert process_pool.duplicate_counts(str(db.path)) == process_pool.count_by_folder(find_duplicate_files(db))


def test_a_real_subprocess_gives_the_same_count(scanned, monkeypatch):
    db, _library, entries = scanned
    monkeypatch.setenv(process_pool.ENV_SUBPROCESS, "1")
    expected = Renamer(db, rescan=None).pending_counts(entries)
    try:
        assert process_pool.submit(os.getpid).result(timeout=120) != os.getpid()    # really another process
        future = process_pool.submit(process_pool.rename_pending_counts, str(db.path))
        assert future.result(timeout=120) == expected
        # a future's waiters are woken before its callbacks run: the pool's count of running jobs follows a moment later
        deadline = time.monotonic() + 10
        while process_pool.busy() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not process_pool.busy()
        assert process_pool.release_if_idle()                           # idle: its processes go
    finally:
        process_pool.shutdown()


def test_subprocesses_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(process_pool.ENV_SUBPROCESS, "0")
    assert not process_pool.enabled()
    future = process_pool.submit(Path, "x")                             # run at once, in this thread
    assert future.done() and future.result() == Path("x") and not process_pool.busy()
