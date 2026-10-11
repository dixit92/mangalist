"""Fakes for the find-pass tests: a nyaa search answering from a list, nyaa's ``.torrent`` downloads from a dict, a
wired :class:`~mangalist.downloads.adapter.Backend` over the fake qBittorrent and the fake Suwayomi, and wanted-list
entries. Made-up names, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from mangalist.downloads.adapter import Backend
from mangalist.downloads.contracts import NyaaCandidate, QbtConnection, SuwayomiConnection
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries
from mangalist.services.nyaa.ranking import order

from .suwayomi_fakes import FakeSuwayomi
from .torrents import MB, PartialQbt, info_hash_of, make_torrent


class FakeSearch:
    """Stands in for NyaaSearch: every query answers *results*; the calls are kept."""

    def __init__(self, results: Sequence[NyaaCandidate] = ()):
        self.results = list(results)
        self.calls: List[tuple] = []
        self.error: Optional[Exception] = None

    def search(self, titles, missing, held):
        self.calls.append((tuple(titles), tuple(missing), tuple(held)))
        if self.error is not None:
            raise self.error
        return order(self.results)


class FakeNyaaClient:
    """``torrent(url)`` from a dict (the .torrent of a pack)."""

    def __init__(self):
        self.files: Dict[str, bytes] = {}
        self.calls: List[str] = []

    def torrent(self, url):
        self.calls.append(url)
        return self.files[url]


def release(n: int, vol_from: Optional[str], vol_to: Optional[str], covers: Sequence[str] = (), *, is_pack=None,
            digital=True, trusted=True, seeders=10, size=1000, info_hash: Optional[str] = None, title=None,
            not_comic=False, url=None) -> NyaaCandidate:
    """A ranked release (rank as the D4 ranking weighs it)."""
    pack = (vol_from != vol_to) if is_pack is None else is_pack
    rank = (len(covers) or (0.5 if vol_from is None else 0)) * 1_000_000.0 + (100_000.0 if digital else 0) \
        + (10_000.0 if trusted else 0) + seeders
    label = title or (f"Series A v{vol_from}-{vol_to} (Digital)" if pack and vol_from else
                      f"Series A v{vol_from} (Digital)" if vol_from else "Series A (Complete pack)")
    return NyaaCandidate(title=label, view_url=f"https://nyaa.example/view/{n}",
                         torrent_url=url if url is not None else f"https://nyaa.example/download/{n}.torrent",
                         info_hash=info_hash or f"{n:040x}", size_bytes=size, seeders=seeders, leechers=0,
                         downloads=1, trusted=trusted, remake=False, published=f"2026-10-0{n % 9 + 1}T00:00:00Z",
                         category="3_1", vol_from=vol_from, vol_to=vol_to, digital=digital, is_pack=pack,
                         not_comic=not_comic, covers_missing=tuple(covers), rank=rank)


def pack_release(n: int, nyaa: FakeNyaaClient, qbt: PartialQbt, names: Sequence[str], vol_from: str, vol_to: str,
                 covers: Sequence[str], size_each: int = 10 * MB) -> NyaaCandidate:
    """A pack whose ``.torrent`` nyaa serves and qBittorrent can add (stopped, with its file list)."""
    url = f"https://nyaa.example/download/{n}.torrent"
    root = f"Series A v{vol_from}-{vol_to} (Digital)"
    files = [(name, size_each) for name in names]
    data = make_torrent(root, files)
    nyaa.files[url] = data
    h = info_hash_of(data)
    qbt.offer(url, h, root, files)
    return release(n, vol_from, vol_to, covers, info_hash=h, title=root, url=url, size=size_each * len(names))


def single_release(n: int, qbt: PartialQbt, volume: str, **kw) -> NyaaCandidate:
    c = release(n, volume, volume, (volume,), **kw)
    qbt.offer(c.torrent_url, c.info_hash, c.title, [(f"{c.title}.cbz", c.size_bytes)])
    return c


def wanted(series_id: int, folder: Path, *, volumes=(), upgrades=(), chapters=(), held=("1",), findable=True,
           title="Series A") -> List[WantedSeries]:
    common = dict(series_id=series_id, folder=str(folder), title=title, held=tuple(held), titles=(title,))
    out = []
    if volumes:
        out.append(WantedSeries(group=GROUP_VOLUMES, gaps="", missing=tuple(volumes), findable=findable, **common))
    if chapters:
        out.append(WantedSeries(group=GROUP_CHAPTERS, gaps="", missing=tuple(chapters), findable=False,
                                reason="needs Suwayomi", **common))
    if upgrades:
        out.append(WantedSeries(group=GROUP_UPGRADES, gaps="", missing=tuple(upgrades), findable=findable, **common))
    return out


@dataclass
class Wired:
    backend: Backend
    search: FakeSearch
    nyaa: FakeNyaaClient
    qbt: PartialQbt
    suwayomi: FakeSuwayomi
    series_id: int
    folder: Path
    entries: list = field(default_factory=list)

    def wanted(self, db, root_ids=None):
        return list(self.entries)


def wire(db, series, tmp_path, *, qbittorrent=True, suwayomi=False) -> Wired:
    save = tmp_path / "torrents" / "mangalist"
    save.mkdir(parents=True, exist_ok=True)
    qbt = PartialQbt(save)
    search, nyaa = FakeSearch(), FakeNyaaClient()
    fake_suwayomi = FakeSuwayomi(tmp_path / "suwayomi")
    sid, folder = series
    holder: list = []
    backend = Backend(db, search=search, nyaa_client=nyaa, client_factory=lambda conn: qbt,
                      chapter_client_factory=lambda conn: fake_suwayomi,
                      wanted=lambda db_, root_ids=None: holder[0].wanted(db_, root_ids))
    if qbittorrent:
        backend.ledger.save_connection(QbtConnection("http://qbt.example:8080", "owner", "pw"))
        backend.ledger.set_save_path(str(save))
    if suwayomi:
        backend.suwayomi.save_connection(SuwayomiConnection("http://suwayomi.example:4567"))
    w = Wired(backend, search, nyaa, qbt, fake_suwayomi, sid, folder)
    holder.append(w)
    return w
