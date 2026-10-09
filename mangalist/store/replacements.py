"""The chapter files a filed volume replaces: one batch per filed download (schema migration 6).

A batch is written when the downloads job (or Check now) looks at a filed download: the chapter files the filed volumes
fully cover, with the size and time they had then, and what happened to them since:

=============  ===============================================================================================
``nothing``    looked at; no chapter file is fully covered (or MangaPixer has no volume list for the series)
``pending``    waiting: delete mode needs the owner's confirmation; holding mode could not move them yet (the
               root busy, the holding folder unusable) and tries again on the next pass
``held``       moved to the holding folder through the journal (``plan_id``); restorable until ``purge_after``
``restored``   moved back by the owner (the plan undone)
``purged``     the retention period ended: deleted from the holding folder (never from the library)
``deleted``    the owner confirmed the list; deleted through the one guarded delete
``declined``   the owner chose to keep them
``failed``     nothing could be done (``error`` says why)
=============  ===============================================================================================

Status changes are compare-and-set (``expect``), like the download ledger's, so a pass and the GUI never act on one
batch twice. :class:`ReplacementStore` wraps a :class:`~mangalist.store.Store`. No Qt here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .db import utcnow
from .schema import REPLACEMENT_MODES, REPLACEMENT_STATUS

META_FIRST_DOWNLOAD = "upgrades.first_download"

#: Statuses the owner still has something to do with (the Download tab shows them).
OPEN = ("pending", "held")


class ReplacementConflict(ValueError):
    """The batch was not in the expected status (another pass or window moved it meanwhile)."""


@dataclass(frozen=True)
class ReplacedFile:
    """One chapter file of a batch, as listed (``modified``: ISO 8601 UTC from the file's mtime, as shown to the owner)."""

    path: str                           # absolute, in the library
    rel: str                            # relative to the series folder, '/' separators
    size: int
    modified: str
    chapters: str                       # e.g. "12", "1-5", "10.1"
    volume: str                         # the filed volume that holds them

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "rel": self.rel, "size": self.size, "modified": self.modified,
                "chapters": self.chapters, "volume": self.volume}


@dataclass(frozen=True)
class Batch:
    id: int
    download_id: int
    series_id: Optional[int]
    root_path: Optional[str]
    series_dir: Optional[str]
    volumes: Tuple[str, ...]
    volume_files: Tuple[Tuple[str, int], ...]   # (absolute path, size) of the filed volume archives
    files: Tuple[ReplacedFile, ...]
    kept: Tuple[Tuple[str, str], ...]           # (rel, why)
    mode: Optional[str]
    status: str
    plan_id: Optional[int]
    holding_dir: Optional[str]
    purge_after: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class NewBatch:
    """What :meth:`ReplacementStore.create` records."""

    download_id: int
    series_id: Optional[int]
    status: str
    root_path: Optional[str] = None
    series_dir: Optional[str] = None
    volumes: Sequence[str] = ()
    volume_files: Sequence[Tuple[str, int]] = ()
    files: Sequence[ReplacedFile] = ()
    kept: Sequence[Tuple[str, str]] = ()
    mode: Optional[str] = None
    error: Optional[str] = None


def _loads(text: Optional[str]) -> list:
    try:
        value = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _file(d: Any) -> Optional[ReplacedFile]:
    if not isinstance(d, dict):
        return None
    try:
        return ReplacedFile(path=str(d["path"]), rel=str(d["rel"]), size=int(d["size"]), modified=str(d["modified"]),
                            chapters=str(d.get("chapters") or ""), volume=str(d.get("volume") or ""))
    except (KeyError, TypeError, ValueError):
        return None


def _batch(r: sqlite3.Row) -> Batch:
    files = tuple(f for f in (_file(d) for d in _loads(r["files"])) if f is not None)
    vol_files = tuple((str(d["path"]), int(d["size"])) for d in _loads(r["volume_files"])
                      if isinstance(d, dict) and "path" in d and "size" in d)
    kept = tuple((str(d.get("rel", "")), str(d.get("why", ""))) for d in _loads(r["kept"]) if isinstance(d, dict))
    return Batch(id=r["id"], download_id=r["download_id"], series_id=r["series_id"], root_path=r["root_path"],
                 series_dir=r["series_dir"], volumes=tuple(str(v) for v in _loads(r["volumes"])),
                 volume_files=vol_files, files=files, kept=kept, mode=r["mode"], status=r["status"],
                 plan_id=r["plan_id"], holding_dir=r["holding_dir"], purge_after=r["purge_after"], error=r["error"],
                 created_at=r["created_at"], updated_at=r["updated_at"])


_KEEP = object()


class ReplacementStore:
    def __init__(self, store):
        self.store = store

    # --- reading -------------------------------------------------------------------------------------

    def get(self, batch_id: int) -> Optional[Batch]:
        with self.store.connect() as con:
            r = con.execute("SELECT * FROM replacements WHERE id = ?", (int(batch_id),)).fetchone()
        return _batch(r) if r is not None else None

    def for_download(self, download_id: int) -> Optional[Batch]:
        with self.store.connect() as con:
            r = con.execute("SELECT * FROM replacements WHERE download_id = ?", (int(download_id),)).fetchone()
        return _batch(r) if r is not None else None

    def with_status(self, *statuses: str) -> List[Batch]:
        marks = ",".join("?" * len(statuses))
        with self.store.connect() as con:
            rows = con.execute(f"SELECT * FROM replacements WHERE status IN ({marks}) ORDER BY id", statuses).fetchall()
        return [_batch(r) for r in rows]

    def all(self) -> List[Batch]:
        with self.store.connect() as con:
            rows = con.execute("SELECT * FROM replacements ORDER BY id").fetchall()
        return [_batch(r) for r in rows]

    def first_download(self) -> int:
        """Downloads with an id up to this were filed before upgrades existed: never looked at."""
        try:
            return int(self.store.get_meta(META_FIRST_DOWNLOAD) or 0)
        except (TypeError, ValueError):
            return 0

    def unexamined(self, download_ids: Sequence[int]) -> List[int]:
        """Those of *download_ids* that came after the upgrade and have no batch yet."""
        first = self.first_download()
        wanted = sorted({int(i) for i in download_ids if int(i) > first})
        if not wanted:
            return []
        marks = ",".join("?" * len(wanted))
        with self.store.connect() as con:
            seen = {r[0] for r in con.execute(f"SELECT download_id FROM replacements WHERE download_id IN ({marks})",
                                              wanted)}
        return [i for i in wanted if i not in seen]

    # --- writing -------------------------------------------------------------------------------------

    def create(self, new: NewBatch) -> Batch:
        if new.status not in REPLACEMENT_STATUS:
            raise ValueError(f"unknown status {new.status!r}")
        if new.mode is not None and new.mode not in REPLACEMENT_MODES:
            raise ValueError(f"unknown mode {new.mode!r}")
        now = utcnow()
        with self.store.connect(durable=True) as con:
            cur = con.execute(
                "INSERT INTO replacements (download_id, series_id, root_path, series_dir, volumes, volume_files, files,"
                " kept, mode, status, error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(new.download_id), new.series_id, new.root_path, new.series_dir,
                 json.dumps(list(new.volumes)),
                 json.dumps([{"path": p, "size": int(s)} for p, s in new.volume_files], ensure_ascii=False),
                 json.dumps([f.as_dict() for f in new.files], ensure_ascii=False),
                 json.dumps([{"rel": r, "why": w} for r, w in new.kept], ensure_ascii=False),
                 new.mode, new.status, new.error, now, now))
            batch_id = int(cur.lastrowid)
        return self.get(batch_id)

    def update(self, batch_id: int, *, expect: Sequence[str], status: Any = _KEEP, mode: Any = _KEEP,
               plan_id: Any = _KEEP, holding_dir: Any = _KEEP, purge_after: Any = _KEEP, error: Any = _KEEP) -> Batch:
        """Change a batch that is in one of the *expect* statuses (else :class:`ReplacementConflict`)."""
        sets, args = ["updated_at = ?"], [utcnow()]
        for name, value in (("status", status), ("mode", mode), ("plan_id", plan_id), ("holding_dir", holding_dir),
                            ("purge_after", purge_after), ("error", error)):
            if value is _KEEP:
                continue
            if name == "status" and value not in REPLACEMENT_STATUS:
                raise ValueError(f"unknown status {value!r}")
            if name == "mode" and value is not None and value not in REPLACEMENT_MODES:
                raise ValueError(f"unknown mode {value!r}")
            sets.append(f"{name} = ?")
            args.append(value)
        marks = ",".join("?" * len(expect))
        with self.store.connect(durable=True) as con:
            cur = con.execute(f"UPDATE replacements SET {', '.join(sets)} WHERE id = ? AND status IN ({marks})",
                              (*args, int(batch_id), *expect))
            if cur.rowcount != 1:
                row = con.execute("SELECT status FROM replacements WHERE id = ?", (int(batch_id),)).fetchone()
                now = row["status"] if row is not None else "gone"
                raise ReplacementConflict(f"replaced-chapters batch {batch_id} is {now}, not {'/'.join(expect)}")
        return self.get(batch_id)
