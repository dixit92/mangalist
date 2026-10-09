"""Upgrades tests: a fresh database, a made-up chapters-only series in a temporary library, a FAKE qBittorrent and a
volume list standing in for MangaPixer's (no network, no real names)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pytest

from mangalist import store, upgrades
from mangalist.downloads.arrivals import run_arrivals
from mangalist.knowledge import VolumeInfo
from mangalist.store.downloads import DownloadLedger

from ..downloads.fakes import HASH, FakeQbt, candidate, data, scan

SERIES = "Series U"
#: The made-up English edition: volume 1 = chapters 1-3, volume 2 = chapters 4-6, volume 3 = chapters 7-9.
VOLUMES = (VolumeInfo("1", "1", "3"), VolumeInfo("2", "4", "6"), VolumeInfo("3", "7", "9"))


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def ledger(db) -> DownloadLedger:
    return DownloadLedger(db)


@pytest.fixture
def library(tmp_path) -> Path:
    return tmp_path / "library" / "Manga"


@pytest.fixture
def holding(tmp_path, db) -> Path:
    folder = tmp_path / "appdata" / "replaced"
    upgrades.set_holding_folder(db, str(folder))
    return folder


@pytest.fixture
def known(monkeypatch):
    """MangaPixer's volume list for the series (a stand-in for the export; ``known[:] = []`` = no list)."""
    volumes: List[VolumeInfo] = list(VOLUMES)
    monkeypatch.setattr(upgrades, "_knowledge_volumes",
                        lambda db, series: (volumes, "") if volumes else (None, "MangaPixer has no volume list"))
    return volumes


def make_series(db, library: Path, chapters: Dict[str, bytes]):
    """A chapters-only series (rel path -> bytes), scanned. Returns (series id, series folder)."""
    sdir = library / SERIES
    sdir.mkdir(parents=True, exist_ok=True)
    for rel, payload in chapters.items():
        p = sdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
    root = db.list_roots()[0] if db.list_roots() else db.add_root(str(library), "Manga")
    scan(db)
    return db.get_series(root.id, SERIES).id, sdir


def chapter_files(numbers, folder: str = "") -> Dict[str, bytes]:
    prefix = f"{folder}/" if folder else ""
    return {f"{prefix}{SERIES} c{n}.cbz": data(f"chapter {n}") for n in numbers}


def file_volumes(ledger, tmp_path, sid, sdir: Path, volumes=("1",), info_hash: str = HASH):
    """Send + finish + file a release holding *volumes* through the real arrivals pass. Returns the record."""
    qbt = FakeQbt(tmp_path / f"torrents-{info_hash[:4]}")
    qbt.save_root.mkdir(parents=True)
    rec = ledger.create(sid, candidate(info_hash, title=f"{SERIES} v{'-'.join(volumes)}"), list(volumes), str(sdir))
    qbt.put(info_hash, f"{SERIES} pack", {f"{SERIES} v{v.zfill(2)} (Digital).cbz": data(f"volume {v}", 9000)
                                          for v in volumes})
    report = run_arrivals(qbt, ledger, remove_completed=False)
    assert rec.id in report.filed, report
    return ledger.get(rec.id)


def names(folder: Path) -> List[str]:
    return sorted(str(p.relative_to(folder)).replace(os.sep, "/") for p in folder.rglob("*") if p.is_file())
