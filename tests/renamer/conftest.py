"""Renamer tests: a fresh database and a made-up library in a temporary folder (no real titles or paths), the FAKE namer,
recorded after-batch hooks (no MangaPixer, no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from mangalist import store
from mangalist.renamer import Renamer

from .fakes import FakeNamer, Recorder


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def library(tmp_path) -> Path:
    folder = tmp_path / "library" / "Manga"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def namer() -> FakeNamer:
    return FakeNamer()


@pytest.fixture
def hooks() -> Recorder:
    return Recorder()


@pytest.fixture
def renamer(db, namer, hooks) -> Renamer:
    return Renamer(db, namer=namer, title_for=lambda root, series: None, rescan=hooks.rescan,
                   request_scans=hooks.request_scans)


def payload(seed: str, size: int = 3000) -> bytes:
    return (seed.encode() * (size // max(1, len(seed)) + 1))[:size]


def make_library(db, library: Path, series: Dict[str, Dict[str, bytes]], **root_settings):
    """Series folders (name -> {rel path: bytes}) in a root, scanned. Returns the root."""
    for title, files in series.items():
        folder = library / title
        folder.mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            p = folder / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    root = next((r for r in db.list_roots() if Path(r.path) == library), None)
    if root is None:
        root = db.add_root(str(library), library.name, **root_settings)
    rescan(db)
    return root


def rescan(db) -> None:
    from mangalist.scanner import record_library_scan, scan_library

    record_library_scan(db, scan_library(db.list_roots(), db=db))


def fmd2(index: int, chapter: str, title: str = "", group: str = "") -> str:
    inner = f"Ch. {chapter}" + (f" - {title}" if title else "") + (f" [{group}]" if group else "")
    return f"{index:04d} [{inner}].cbz"
