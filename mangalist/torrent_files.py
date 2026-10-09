"""A small bencode reader: what files a ``.torrent`` holds, and its info hash. Pure Python, no Qt, no I/O.

MangaList reads a pack's ``.torrent`` (from nyaa) before sending it, to show "3 of 23 files" and to check that the file
is the release the search listed. Only what that needs is read: the torrent's name, the files (path and size) and the
hash of the ``info`` dictionary. The reader is strict about the format (a malformed file is refused, never half-read)
and bounded (nesting depth, number of files), because the bytes come from the internet.

Names are written the way qBittorrent reports them: a multi-file torrent's files are ``<torrent name>/<path>``, a
single-file torrent's file is its name. BEP 47 padding files (``attr`` contains ``p``) are not files and are left out.
Version 2 only torrents (a ``file tree`` and no file list) are refused: nyaa's are version 1 or hybrid.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

MAX_DEPTH = 32
MAX_FILES = 20000
MAX_BYTES = 16 * 1024 * 1024


class TorrentError(ValueError):
    """The bytes are not a readable ``.torrent`` (the message says why, in plain words)."""


@dataclass(frozen=True)
class TorrentEntry:
    name: str                           # '/'-separated, as qBittorrent lists it
    size: int


@dataclass(frozen=True)
class TorrentListing:
    name: str
    files: Tuple[TorrentEntry, ...]
    info_hash: str                      # lowercase hex SHA-1 of the info dictionary (the version 1 hash)
    info_hash_v2: str                   # lowercase hex SHA-256 of it (the version 2 hash, 64 characters)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def has_hash(self, info_hash: str) -> bool:
        """True when *info_hash* (40 or 64 hex characters, any case) is this torrent's."""
        h = (info_hash or "").strip().lower()
        return bool(h) and h in (self.info_hash, self.info_hash_v2)


# --- bencode ---------------------------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.info_span: Optional[Tuple[int, int]] = None      # where the top-level 'info' value lies

    def value(self, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            raise TorrentError("the torrent is nested too deeply")
        if self.pos >= len(self.data):
            raise TorrentError("the torrent ends too early")
        c = self.data[self.pos:self.pos + 1]
        if c == b"i":
            return self._int()
        if c == b"l":
            self.pos += 1
            items: List[Any] = []
            while self._peek() != b"e":
                items.append(self.value(depth + 1))
            self.pos += 1
            return items
        if c == b"d":
            return self._dict(depth)
        if c.isdigit():
            return self._bytes()
        raise TorrentError(f"unexpected byte at position {self.pos}")

    def _peek(self) -> bytes:
        if self.pos >= len(self.data):
            raise TorrentError("the torrent ends too early")
        return self.data[self.pos:self.pos + 1]

    def _number(self, end: bytes) -> int:
        stop = self.data.find(end, self.pos)
        if stop < 0:
            raise TorrentError("the torrent ends too early")
        text = self.data[self.pos:stop]
        body = text[1:] if text.startswith(b"-") else text
        if not body.isdigit() or (len(body) > 1 and body.startswith(b"0")) or text == b"-0" or len(body) > 19:
            raise TorrentError(f"a number is malformed at position {self.pos}")
        self.pos = stop + 1
        return int(text)

    def _int(self) -> int:
        self.pos += 1
        return self._number(b"e")

    def _bytes(self) -> bytes:
        size = self._number(b":")
        if size < 0 or self.pos + size > len(self.data):
            raise TorrentError("a string is longer than the torrent")
        out = self.data[self.pos:self.pos + size]
        self.pos += size
        return out

    def _dict(self, depth: int) -> dict:
        self.pos += 1
        out: dict = {}
        while self._peek() != b"e":
            if not self._peek().isdigit():
                raise TorrentError("a dictionary key is not a string")
            key = self._bytes()
            if key in out:
                raise TorrentError("a dictionary repeats a key")
            start = self.pos
            out[key] = self.value(depth + 1)
            if depth == 0 and key == b"info":
                self.info_span = (start, self.pos)
        self.pos += 1
        return out


def decode(data: bytes) -> Any:
    """One bencoded value (the whole of *data*). Raises :class:`TorrentError`."""
    return _decode(data)[0]


def _decode(data: bytes) -> Tuple[Any, _Reader]:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise TorrentError("the torrent is empty")
    if len(data) > MAX_BYTES:
        raise TorrentError("the torrent is unexpectedly large")
    reader = _Reader(bytes(data))
    value = reader.value()
    if reader.pos != len(reader.data):
        raise TorrentError("there is extra data after the torrent")
    return value, reader


# --- the listing -----------------------------------------------------------------------------------------

def _text(raw: Any, what: str) -> str:
    if not isinstance(raw, bytes):
        raise TorrentError(f"{what} is missing")
    return raw.decode("utf-8", errors="replace")


def _size(raw: Any, what: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise TorrentError(f"{what} has no usable size")
    return raw


def _part(raw: Any) -> str:
    text = _text(raw, "a file path")
    if text in ("", ".", "..") or "\x00" in text:
        raise TorrentError("a file path is not safe")
    return text.replace("/", "_").replace("\\", "_")


def read_torrent(data: bytes) -> TorrentListing:
    """The files and hashes of a ``.torrent`` file's bytes. Raises :class:`TorrentError`."""
    top, reader = _decode(data)
    if not isinstance(top, dict) or not isinstance(top.get(b"info"), dict) or reader.info_span is None:
        raise TorrentError("this is not a torrent file (no info section)")
    info = top[b"info"]
    start, end = reader.info_span
    raw_info = reader.data[start:end]
    name = _part(info.get(b"name.utf-8", info.get(b"name")))
    files: List[TorrentEntry] = []
    if isinstance(info.get(b"files"), list):
        for row in info[b"files"]:
            if not isinstance(row, dict):
                raise TorrentError("a file entry is malformed")
            attr = row.get(b"attr")
            if isinstance(attr, bytes) and b"p" in attr:        # BEP 47 padding: not a file
                continue
            path = row.get(b"path.utf-8", row.get(b"path"))
            if not isinstance(path, list) or not path:
                raise TorrentError("a file has no path")
            files.append(TorrentEntry("/".join([name] + [_part(p) for p in path]), _size(row.get(b"length"), "a file")))
            if len(files) > MAX_FILES:
                raise TorrentError(f"the torrent lists more than {MAX_FILES} files")
    elif b"length" in info:
        files.append(TorrentEntry(name, _size(info[b"length"], "the file")))
    elif b"file tree" in info:
        raise TorrentError("this is a version 2 torrent without a file list; MangaList cannot read it")
    else:
        raise TorrentError("the torrent lists no files")
    if not files:
        raise TorrentError("the torrent lists no files")
    return TorrentListing(name, tuple(files), hashlib.sha1(raw_info).hexdigest(), hashlib.sha256(raw_info).hexdigest())


__all__ = ["MAX_FILES", "TorrentEntry", "TorrentError", "TorrentListing", "decode", "read_torrent"]
