"""Downloads tests: a fresh database, a synthetic library and a FAKE qBittorrent (no network, made-up names)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mangalist import store
from mangalist.store.downloads import DownloadLedger

from .fakes import HASH, FakeQbt, candidate, data, scan  # noqa: F401 - re-exported for the tests


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def ledger(db) -> DownloadLedger:
    return DownloadLedger(db)


@pytest.fixture
def library(tmp_path) -> Path:
    root = tmp_path / "library" / "Manga"
    (root / "Series A").mkdir(parents=True)
    (root / "Series A" / "Series A v01 (Digital).cbz").write_bytes(data("held v01"))
    return root


@pytest.fixture
def series(db, library):
    """(series id, series folder) of 'Series A' after a recorded scan."""
    root = db.add_root(str(library), "Manga")
    scan(db)
    row = db.get_series(root.id, "Series A")
    return row.id, Path(os.path.join(library, "Series A"))


@pytest.fixture
def qbt(tmp_path) -> FakeQbt:
    save = tmp_path / "torrents" / "mangalist"
    save.mkdir(parents=True)
    return FakeQbt(save)
