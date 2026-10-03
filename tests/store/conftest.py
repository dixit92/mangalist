"""Store tests: a fresh database in the per-test data folder (tests/conftest.py sets it)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from manga_list import paths, store


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    st = store.get_store()
    assert st.path == paths.db_file()
    return st


@pytest.fixture
def library(tmp_path):
    """A synthetic library root (names made up for the tests)."""
    root = tmp_path / "library" / "Manga"
    root.mkdir(parents=True)
    return root


def make_archive(path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


@pytest.fixture
def live_pid():
    """The pid of another live process on this host."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()
