"""Archive rows: one per archive in any root (schema 4), the per-archive identity series carry-over rests on.

A row holds the archive's root, its series row, its root-relative path, size and mtime (ns), MangaPixer's v1
content signature (NULL until the background backfill signed it; cleared when the file changes in place),
present / missing, first / last seen (time and scan revision) and ``missing_since``. The scan keeps the rows of
every root in step (:mod:`mangalist.identity.record`); a journal move re-points them directly
(:meth:`ArchivesMixin.follow_journal_move`). Units are NOT tied to these rows: they are computed from the names
on every scan, as before.

No Qt here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .db import utcnow

DEFAULT_MOVE_WINDOW_DAYS = 30
MOVE_WINDOW_SETTING = "move_window_days"     # settings key (int days); MangaPixer: the trash retention
SCAN_REVISION_META = "identity.scan"


@dataclass
class Archive:
    id: int
    root_id: int
    series_id: Optional[int]
    rel_path: str
    size: int
    mtime_ns: int
    signature: Optional[str]
    status: str
    first_seen_at: str
    last_seen_at: str
    missing_since: Optional[str]
    first_seen_scan: int
    last_seen_scan: int


def archive_of(r: sqlite3.Row) -> Archive:
    return Archive(id=r["id"], root_id=r["root_id"], series_id=r["series_id"], rel_path=r["rel_path"], size=r["size"],
                   mtime_ns=r["mtime_ns"], signature=r["signature"], status=r["status"],
                   first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"], missing_since=r["missing_since"],
                   first_seen_scan=r["first_seen_scan"], last_seen_scan=r["last_seen_scan"])


def next_scan_revision(con: sqlite3.Connection) -> int:
    """Bump and return the scan revision (one per recorded library scan; orders "seen" without clock ties)."""
    r = con.execute("SELECT value FROM meta WHERE key = ?", (SCAN_REVISION_META,)).fetchone()
    try:
        rev = int(r["value"]) + 1 if r is not None and r["value"] is not None else 1
    except (TypeError, ValueError):
        rev = 1
    con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SCAN_REVISION_META, str(rev)))
    return rev


def _under(rel: str, folder_rel: str) -> bool:
    return rel.startswith(folder_rel.rstrip("/") + "/")


class ArchivesMixin:
    def list_archives(self, root_id: Optional[int] = None, series_id: Optional[int] = None,
                      status: Optional[str] = None) -> List[Archive]:
        sql, args = "SELECT * FROM archives WHERE 1=1", []
        for col, val in (("root_id", root_id), ("series_id", series_id), ("status", status)):
            if val is not None:
                sql += f" AND {col} = ?"
                args.append(val)
        with self.connect() as con:
            rows = con.execute(sql + " ORDER BY root_id, rel_path", args).fetchall()
        return [archive_of(r) for r in rows]

    def get_archive(self, archive_id: int) -> Optional[Archive]:
        with self.connect() as con:
            r = con.execute("SELECT * FROM archives WHERE id = ?", (archive_id,)).fetchone()
        return archive_of(r) if r else None

    def archive_at(self, root_id: int, rel_path: str) -> Optional[Archive]:
        with self.connect() as con:
            r = con.execute("SELECT * FROM archives WHERE root_id = ? AND rel_path = ?", (root_id, rel_path)).fetchone()
        return archive_of(r) if r else None

    def unsigned_count(self) -> int:
        """Present archives still waiting for their content signature."""
        with self.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM archives WHERE status = 'present' AND signature IS NULL"
                                   ).fetchone()[0])

    def move_window_days(self) -> int:
        """How long a missing archive stays a move candidate (setting ``move_window_days``, default 30)."""
        try:
            days = int(self.get_setting(MOVE_WINDOW_SETTING, DEFAULT_MOVE_WINDOW_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_MOVE_WINDOW_DAYS
        return max(0, days)

    def follow_journal_move(self, src, dst, is_dir: bool) -> int:
        """A journal step moved *src* to *dst* (absolute paths inside roots): re-point the archive rows
        directly (no detection needed) - the file's row, or every row under a moved folder - and record each
        as an archive move (``how='journal'``). The rows take the series row that now holds them. Returns the
        number of rows re-pointed."""
        old_loc, new_loc = self._locate(src), self._locate(dst)
        if old_loc is None or new_loc is None:
            return 0
        (old_root, old_rel), (new_root, new_rel) = old_loc, new_loc
        now = utcnow()
        moved = 0
        with self.connect() as con:
            if is_dir:
                rows = [r for r in con.execute("SELECT * FROM archives WHERE root_id = ? AND substr(rel_path, 1, ?) = ?",
                                               (old_root, len(old_rel) + 1, old_rel.rstrip("/") + "/"))]
                targets = [(r, new_rel.rstrip("/") + "/" + r["rel_path"][len(old_rel.rstrip("/")) + 1:]) for r in rows]
            else:
                r = con.execute("SELECT * FROM archives WHERE root_id = ? AND rel_path = ?", (old_root, old_rel)).fetchone()
                targets = [(r, new_rel)] if r is not None else []
            series_rows = [(s["id"], s["rel_path"]) for s in
                           con.execute("SELECT id, rel_path FROM series WHERE root_id = ? AND status = 'present'", (new_root,))]
            for r, rel in targets:
                if con.execute("SELECT 1 FROM archives WHERE root_id = ? AND rel_path = ? AND id != ?",
                               (new_root, rel, r["id"])).fetchone():
                    continue    # a row of another file is already there; the next scan sorts it out
                holder = _series_holding(series_rows, rel)
                con.execute("UPDATE archives SET root_id = ?, rel_path = ?, series_id = COALESCE(?, series_id) WHERE id = ?",
                            (new_root, rel, holder, r["id"]))
                con.execute("INSERT INTO archive_moves (archive_id, from_series_id, to_series_id, from_root_id, from_path,"
                            " to_root_id, to_path, how, scan, at) VALUES (?,?,?,?,?,?,?, 'journal', NULL, ?)",
                            (r["id"], r["series_id"], holder if holder is not None else r["series_id"], r["root_id"],
                             r["rel_path"], new_root, rel, now))
                moved += 1
        return moved


def _series_holding(series_rows: List[Tuple[int, str]], rel: str) -> Optional[int]:
    """The series row whose folder holds the root-relative archive path *rel* (the deepest one)."""
    best: Optional[Tuple[int, int]] = None
    for sid, srel in series_rows:
        if _under(rel, srel) and (best is None or len(srel) > best[1]):
            best = (sid, len(srel))
    return best[0] if best else None


def series_holding(series_rows: List[Tuple[int, str]], rel: str) -> Optional[int]:
    return _series_holding(series_rows, rel)


def abs_path(root_path: str, rel_path: str) -> Path:
    return Path(root_path).joinpath(*rel_path.split("/"))
