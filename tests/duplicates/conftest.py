"""A synthetic library on disk, scanned the way the app does: real files in a temp folder, their names read by the real
parser, the units stored in a fresh database. Made-up series names only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from mangalist import paths, store
from mangalist.inventory import units_from_parsed
from mangalist.parsing import ParseContext, parse_name
from mangalist.store import SeriesSeen


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    st = store.get_store()
    assert st.path == paths.db_file()
    return st


class Library:
    def __init__(self, db, base: Path, name: str = "Manga"):
        self.db = db
        self.dir = base / name
        self.dir.mkdir(parents=True)
        self.root = db.add_root(str(self.dir), name)
        self.files: Dict[str, List[str]] = {}       # series folder -> archive rel paths
        self.hints: Dict[str, Optional[str]] = {}

    def add(self, series: str, rel: str, size: int = 10, mtime_ns: Optional[int] = None,
            hint: Optional[str] = None) -> Path:
        path = self.dir / series / Path(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))
        self.files.setdefault(series, []).append(rel)
        if hint:
            self.hints[series] = hint
        return path

    def scan(self) -> "Library":
        """Parse every added archive and store the units (what a scan leaves in the database)."""
        self.db.record_scan(self.root.id, self.dir, [SeriesSeen(s, f"fp-{s}", len(r)) for s, r in self.files.items()])
        by_series = {}
        for series, rels in self.files.items():
            sid = self.db.get_series(self.root.id, series).id
            ctx = ParseContext(kind_hint=self.hints.get(series))
            by_series[sid] = {
                rel: units_from_parsed(rel, parse_name(rel.rsplit("/", 1)[-1], ctx), (self.dir / series / rel).stat().st_size)
                for rel in rels}
        self.db.sync_units(by_series)
        self._record_archives()
        return self

    def _record_archives(self) -> None:
        """The archive rows a scan leaves (path, size, mtime), written directly: the identity pass is not under test here."""
        with self.db.connect() as con:
            for series, rels in self.files.items():
                sid = self.series_id(series)
                for rel in rels:
                    path = self.dir / series / Path(*rel.split("/"))
                    if not path.exists():
                        continue
                    st = path.stat()
                    con.execute(
                        "INSERT OR REPLACE INTO archives (root_id, series_id, rel_path, size, mtime_ns, status, first_seen_at,"
                        " last_seen_at) VALUES (?,?,?,?,?, 'present', 'then', 'now')",
                        (self.root.id, sid, f"{series}/{rel}", st.st_size, st.st_mtime_ns))

    def series_id(self, series: str) -> int:
        return self.db.get_series(self.root.id, series).id


@pytest.fixture
def lib(db, tmp_path) -> Library:
    return Library(db, tmp_path / "library")
