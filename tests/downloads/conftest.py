"""Downloads tests: a fresh database, a synthetic library and a FAKE qBittorrent (no network, made-up names)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List

import pytest

from mangalist import store
from mangalist.downloads.contracts import NyaaCandidate, TorrentFile, TorrentInfo
from mangalist.store.downloads import DownloadLedger

HASH = "ab" * 20


class FakeQbt:
    """A TorrentClient that keeps torrents in memory and their files in a temporary folder; ``delete`` removes the
    torrent's data the way qBittorrent does."""

    def __init__(self, save_root: Path):
        self.save_root = save_root
        self.infos: Dict[str, TorrentInfo] = {}
        self.file_lists: Dict[str, List[TorrentFile]] = {}
        self.calls: List[tuple] = []
        self.unreachable = False

    # --- test helpers ---
    def put(self, info_hash: str, name: str, files: Dict[str, bytes], *, state: str = "uploading",
            progress: float = 1.0, category: str = "mangalist", partial: Dict[str, float] = None) -> TorrentInfo:
        folder = self.save_root / name
        for rel, data in files.items():
            p = folder / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        info = TorrentInfo(info_hash, name, category, state, progress, str(self.save_root), str(folder), 1.0, 60)
        self.infos[info_hash] = info
        self.file_lists[info_hash] = [TorrentFile(f"{name}/{rel}", len(data), (partial or {}).get(rel, 1.0))
                                      for rel, data in files.items()]
        return info

    def set(self, info_hash: str, **changes) -> None:
        from dataclasses import replace

        self.infos[info_hash] = replace(self.infos[info_hash], **changes)

    # --- TorrentClient ---
    def version(self) -> str:
        return "v5.2.4"

    def ensure_category(self, name: str, save_path: str) -> None:
        self.calls.append(("ensure_category", name, save_path))

    def add(self, url: str, *, category: str) -> None:
        self.calls.append(("add", url, category))

    def torrents(self, category: str):
        if self.unreachable:
            raise ConnectionError("connection refused")
        return [t for t in self.infos.values() if t.category == category]

    def files(self, info_hash: str):
        return list(self.file_lists.get(info_hash, []))

    def delete(self, info_hash: str, *, delete_files: bool) -> None:
        self.calls.append(("delete", info_hash, delete_files))
        info = self.infos.pop(info_hash)
        if delete_files:
            shutil.rmtree(info.content_path, ignore_errors=True)

    @property
    def deleted(self) -> List[str]:
        return [c[1] for c in self.calls if c[0] == "delete"]


def candidate(info_hash: str = HASH, **kw) -> NyaaCandidate:
    base = dict(title="Series A v02-04 (Digital) (Group)", view_url="https://nyaa.example/view/1",
                torrent_url="https://nyaa.example/download/1.torrent", info_hash=info_hash, size_bytes=1000,
                seeders=5, leechers=0, downloads=10, trusted=False, remake=False,
                published="2026-10-01T00:00:00Z", category="3_1", vol_from="2", vol_to="4", is_pack=True)
    base.update(kw)
    return NyaaCandidate(**base)


def data(seed: str, size: int = 5000) -> bytes:
    return (seed.encode() * (size // max(1, len(seed)) + 1))[:size]


def scan(db) -> None:
    from mangalist.scanner import record_library_scan, scan_library

    record_library_scan(db, scan_library(db.list_roots(), db=db))


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
