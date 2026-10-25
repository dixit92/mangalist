"""The page-hash cache and the dismissed overlaps (schema 11; :mod:`mangalist.duplicate_verdicts`).

- ``page_hashes``: an archive's page hashes (:mod:`mangalist.page_hash`) by its content signature and the hash
  algorithm. Only results that depend on the file's bytes are stored - ``ok`` and ``unreadable`` (a broken zip, pages
  that cannot be decoded); a file that changed while it was read, an unsupported kind (RAR, 7z) or a missing Pillow is
  never stored, so it is tried again later.
- ``overlap_dismissals``: an item of "Whole chapters with parts" the owner dismissed ("this is fine"), keyed by its
  files' signatures (:func:`dismissal_key`): the same files stay dismissed after a rename or a move; a changed or an
  added file brings the item back.

Plain functions on a :class:`~mangalist.store.Store` (no mixin), no Qt.
"""

from __future__ import annotations

import hashlib
import json
import struct
from typing import Dict, Iterable, Optional, Sequence, Set

from ..page_hash import ALGORITHM, STATUS_OK, STATUS_UNREADABLE, ArchivePages
from .db import utcnow

_CHUNK = 400                            # signatures per IN (...) query


def _pack(values: Sequence[Optional[int]]) -> bytes:
    return b"".join(struct.pack(">Q", (v or 0) & 0xFFFF_FFFF_FFFF_FFFF) for v in values)


def _unpack(blob: Optional[bytes]) -> list:
    blob = blob or b""
    return [struct.unpack_from(">Q", blob, i)[0] for i in range(0, len(blob) - len(blob) % 8, 8)]


def store_pages(db, signature: str, pages: ArchivePages, algorithm: str = ALGORITHM) -> bool:
    """Remember *pages* for the archive signed *signature*. False (nothing stored) for a result that does not depend
    on the file's bytes alone (see the module docstring)."""
    if not signature or pages.status not in (STATUS_OK, STATUS_UNREADABLE):
        return False
    unread = ",".join(str(i) for i, h in enumerate(pages.hashes) if h is None)
    crcs = b"".join(struct.pack(">II", crc & 0xFFFF_FFFF, size & 0xFFFF_FFFF) for crc, size in pages.crcs)
    with db.connect() as con:
        con.execute("INSERT INTO page_hashes (signature, algorithm, status, pages, hashes, unread, crcs, reason,"
                    " seconds, hashed_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(signature, algorithm) DO UPDATE SET status = excluded.status, pages = excluded.pages,"
                    " hashes = excluded.hashes, unread = excluded.unread, crcs = excluded.crcs,"
                    " reason = excluded.reason, seconds = excluded.seconds, hashed_at = excluded.hashed_at",
                    (signature, algorithm, pages.status, pages.pages, _pack(pages.hashes), unread or None, crcs,
                     pages.reason or None, float(pages.seconds), utcnow()))
    return True


def cached_pages(db, signatures: Iterable[str], algorithm: str = ALGORITHM) -> Dict[str, ArchivePages]:
    """signature -> the stored pages, for those of *signatures* that have a row."""
    wanted = sorted({s for s in signatures if s})
    out: Dict[str, ArchivePages] = {}
    with db.connect() as con:
        for i in range(0, len(wanted), _CHUNK):
            part = wanted[i:i + _CHUNK]
            rows = con.execute(f"SELECT signature, status, pages, hashes, unread, crcs, reason, seconds FROM page_hashes"
                               f" WHERE algorithm = ? AND signature IN ({','.join('?' * len(part))})",
                               [algorithm, *part]).fetchall()
            for r in rows:
                values = _unpack(r["hashes"])[:int(r["pages"] or 0)]
                bad = {int(x) for x in (r["unread"] or "").split(",") if x.strip().isdigit()}
                hashes = tuple(None if i in bad else v for i, v in enumerate(values))
                blob = r["crcs"] or b""
                crcs = tuple(struct.unpack_from(">II", blob, j) for j in range(0, len(blob) - len(blob) % 8, 8))
                out[r["signature"]] = ArchivePages(r["status"], hashes, crcs, r["reason"] or "", float(r["seconds"] or 0))
    return out


def hashed_count(db, algorithm: str = ALGORITHM) -> int:
    with db.connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM page_hashes WHERE algorithm = ?", (algorithm,)).fetchone()[0])


# --- dismissed overlaps ---------------------------------------------------------------------------------------

def dismissal_key(signatures: Iterable[Optional[str]]) -> Optional[str]:
    """The key of an item made of files with these signatures; None when one of them is unknown (it cannot be told
    whether the files changed, so it cannot be dismissed)."""
    sigs = list(signatures)
    if not sigs or any(not s for s in sigs):
        return None
    return hashlib.sha256("\n".join(sorted(sigs)).encode("utf-8")).hexdigest()


def dismiss(db, key: str, *, series_id: Optional[int], folder: str, number: str, files: Sequence[str]) -> None:
    with db.connect() as con:
        con.execute("INSERT INTO overlap_dismissals (key, series_id, folder, number, files, dismissed_at)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET dismissed_at = excluded.dismissed_at",
                    (key, series_id, folder, number, json.dumps(list(files)), utcnow()))


def undismiss(db, key: str) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM overlap_dismissals WHERE key = ?", (key,)).rowcount > 0


def dismissed_keys(db) -> Set[str]:
    with db.connect() as con:
        return {r[0] for r in con.execute("SELECT key FROM overlap_dismissals")}


__all__ = ["store_pages", "cached_pages", "hashed_count", "dismissal_key", "dismiss", "undismiss", "dismissed_keys"]
