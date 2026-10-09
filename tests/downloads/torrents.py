"""Builders for the partial-download tests: bencode, a made-up ``.torrent`` and a FAKE qBittorrent that understands the
stopped add, the file list, ``filePrio`` and start / stop. Made-up names and sizes only; nothing touches a network."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from mangalist.downloads.contracts import TorrentFile, TorrentInfo

from .fakes import FakeQbt

MB = 1024 * 1024


def bencode(value) -> bytes:
    if isinstance(value, bool):
        raise TypeError("no booleans in bencode")
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(
            (k if isinstance(k, bytes) else k.encode(), v) for k, v in value.items())) + b"e"
    raise TypeError(type(value))


def make_torrent(name: str, files: Optional[Sequence[Tuple[str, int]]] = None, *, length: Optional[int] = None,
                 extra_info: Optional[dict] = None, announce: str = "https://tracker.example/announce") -> bytes:
    """A multi-file torrent (``files`` = ``(path/inside, size)`` pairs) or, with ``length``, a single-file one."""
    info: dict = {"name": name, "piece length": 262144, "pieces": b"\x00" * 20}
    if files is not None:
        info["files"] = [{"length": size, "path": path.split("/")} for path, size in files]
    else:
        info["length"] = length if length is not None else 1000
    info.update(extra_info or {})
    return bencode({"announce": announce, "info": info})


def info_hash_of(torrent: bytes, *, v2: bool = False) -> str:
    """Hash the info dictionary of *torrent* (found by decoding it with the module under test's own reader)."""
    from mangalist.torrent_files import read_torrent

    listing = read_torrent(torrent)
    return listing.info_hash_v2 if v2 else listing.info_hash


class PartialQbt(FakeQbt):
    """A TorrentClient with the partial-download calls. ``add`` of a known URL makes its torrent appear after
    ``appear_after`` calls of ``torrents`` (qBittorrent fetches the .torrent after answering the add); a torrent is
    ``stoppedDL`` until ``start``. ``fail`` maps a method name to an exception raised by it."""

    def __init__(self, save_root):
        super().__init__(save_root)
        self.packs: Dict[str, Tuple[str, str, List[Tuple[str, int]]]] = {}    # url -> (hash, name, [(path, size)])
        self.appear_after = 0
        self.fail: Dict[str, Exception] = {}
        self.states: List[str] = []                       # the torrent's state each time start / stop was called
        self._pending: Dict[str, int] = {}
        self.rows: Dict[str, List[dict]] = {}
        self.ignore_priorities = False                    # a qBittorrent that "accepts" filePrio and keeps nothing

    # --- test helpers ---
    def offer(self, url: str, info_hash: str, name: str, files: Sequence[Tuple[str, int]]) -> None:
        self.packs[url] = (info_hash, name, list(files))

    def prio_of(self, info_hash: str) -> Dict[str, int]:
        return {r["name"]: r["priority"] for r in self.rows[info_hash]}

    def calls_named(self, name: str) -> List[tuple]:
        return [c for c in self.calls if c[0] == name]

    # --- TorrentClient ---
    def add(self, url: str, *, category: str, stopped: bool = False) -> None:
        self.calls.append(("add", url, category, stopped))
        if "add" in self.fail:
            raise self.fail["add"]
        if url.startswith("magnet:"):
            raise RuntimeError("a magnet link is not offered by this fake")
        h, name, files = self.packs[url]
        if h in self.infos:
            raise RuntimeError("qBittorrent did not add the torrent: already in qBittorrent")
        self._pending[h] = self.appear_after
        multi = len(files) > 1 or "/" in files[0][0]
        self.rows[h] = [{"name": f"{name}/{path}" if multi else path, "size": size, "progress": 0.0, "index": i,
                         "priority": 1} for i, (path, size) in enumerate(files)]
        self.infos[h] = TorrentInfo(h, name, category, "stoppedDL" if stopped else "downloading", 0.0,
                                    str(self.save_root), str(self.save_root / name), 0.0, 0, max_ratio=2.0,
                                    max_seeding_time=87600)

    def torrents(self, category: str):
        if self.unreachable:
            raise ConnectionError("connection refused")
        out = []
        for t in self.infos.values():
            if t.info_hash in self._pending and self._pending[t.info_hash] > 0:
                self._pending[t.info_hash] -= 1
                continue
            if t.category == category:
                out.append(t)
        return out

    def files(self, info_hash: str):
        if info_hash in self._pending and self._pending[info_hash] > 0:
            raise LookupError("no such torrent yet")
        if info_hash not in self.rows:
            return list(self.file_lists.get(info_hash, []))
        return [TorrentFile(r["name"], r["size"], r["progress"], r["index"], r["priority"]) for r in self.rows[info_hash]]

    def set_file_priority(self, info_hash: str, file_ids: Sequence[int], priority: int) -> None:
        self.calls.append(("set_file_priority", info_hash, tuple(sorted(file_ids)), priority))
        if "set_file_priority" in self.fail:
            raise self.fail["set_file_priority"]
        if self.ignore_priorities:
            return
        for r in self.rows[info_hash]:
            if r["index"] in file_ids:
                r["priority"] = priority

    def start(self, info_hash: str) -> None:
        self.calls.append(("start", info_hash))
        self.states.append(self.infos[info_hash].state)
        if "start" in self.fail:
            raise self.fail["start"]
        self.infos[info_hash] = replace(self.infos[info_hash], state="downloading")

    def stop(self, info_hash: str) -> None:
        self.calls.append(("stop", info_hash))
        if info_hash in self.infos:
            self.infos[info_hash] = replace(self.infos[info_hash], state="stoppedDL")

    def delete(self, info_hash: str, *, delete_files: bool) -> None:
        self.rows.pop(info_hash, None)
        super().delete(info_hash, delete_files=delete_files)

    # --- the download finishing (for the arrivals tests) ---
    def finish(self, info_hash: str, payloads: Dict[str, bytes]) -> None:
        """qBittorrent has downloaded every file whose priority is above 0: write them, mark them done; the torrent
        reports 100 % for what it was asked to get and goes on seeding."""
        info = self.infos[info_hash]
        base = self.save_root
        for r in self.rows[info_hash]:
            if r["priority"] > 0:
                path = base / r["name"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payloads.get(r["name"], b"x" * r["size"]))
                r["progress"] = 1.0
        self.infos[info_hash] = replace(info, state="uploading", progress=1.0)
