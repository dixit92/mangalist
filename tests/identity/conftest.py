"""Identity tests: a fresh database per test and synthetic library trees only (made-up names and bytes)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mangalist import store


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def library(tmp_path) -> Path:
    root = tmp_path / "library" / "Manga"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def library2(tmp_path) -> Path:
    root = tmp_path / "library" / "Completed"
    root.mkdir(parents=True)
    return root


def content(seed: str, size: int = 4000) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < size:
        out += hashlib.sha256(f"{seed}:{i}".encode()).digest()
        i += 1
    return bytes(out[:size])


def make_archive(path: Path, seed=None, size: int = 4000) -> Path:
    """An archive with its own bytes (seed: the name by default); same seed + size = an identical copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content(seed if seed is not None else path.name, size))
    return path


def scan(db):
    """The GUI's path: scan every root, record it. Returns the LibraryScan (``.renamed`` set, ``.identity``)."""
    from mangalist.scanner import record_library_scan, scan_library

    result = scan_library(db.list_roots(), db=db)
    result.renamed = record_library_scan(db, result)
    return result


def sign(db):
    from mangalist.identity.backfill import backfill_signatures

    return backfill_signatures(db, per_file_delay=0)


def entry(result, name):
    return next(e for e in result.entries if Path(e.folder).name == name)


def record_with(db, *, signer=None, now=None):
    """Scan + series rows + archive rows with a given signer / clock (no units, no MangaPixer layer)."""
    from mangalist.identity.moves import record_archives
    from mangalist.scanner import scan_library
    from mangalist.store.series import seen_from_entries

    result = scan_library(db.list_roots(), db=db)
    for rs in result.roots:
        db.record_scan(rs.root_id, rs.folder, seen_from_entries(rs.folder, rs.entries))
    return record_archives(db, [(rs.root_id, rs.folder, rs.entries) for rs in result.roots], signer=signer, now=now)
