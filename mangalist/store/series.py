"""Series rows: root-relative identity, folder fingerprint, MangaUpdates identity, and the journal re-link of a
moved series folder. A folder renamed or moved by hand is recognised by its archives (:mod:`mangalist.identity`)."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .db import utcnow
from .exclusions import rel_posix
from .schema import SERIES_KIND_HINTS

FINGERPRINT_VERSION = "v1"


def folder_fingerprint(sizes: Iterable[int]) -> Optional[str]:
    """A series folder's fingerprint: the multiset of its archive sizes.

    Names are left out on purpose, so renaming the folder (or the files inside it) keeps the
    fingerprint; adding, removing or changing an archive changes it. ``None`` for a folder without
    archives (nothing to recognise it by).
    """
    ordered = sorted(int(s) for s in sizes)
    if not ordered:
        return None
    digest = hashlib.sha256(",".join(map(str, ordered)).encode("ascii")).hexdigest()[:32]
    return f"{FINGERPRINT_VERSION}:{len(ordered)}:{digest}"


@dataclass
class Series:
    id: int
    root_id: int
    rel_path: str
    fingerprint: Optional[str]
    n_archives: int
    mu_id: Optional[int]
    mu_confirmed: bool
    status: str
    first_seen_at: str
    last_seen_at: str
    kind_hint: Optional[str] = None   # the owner's "volumes or chapters?" answer (C12): volumes | chapters
    missing_since: Optional[str] = None


@dataclass
class SeriesSeen:
    """One series folder found by a scan."""

    rel_path: str
    fingerprint: Optional[str]
    n_archives: int


@dataclass
class ScanRecord:
    added: List[str] = field(default_factory=list)
    relinked: List[Tuple[str, str]] = field(default_factory=list)  # (old rel, new rel); always empty since schema 4
    missing: List[str] = field(default_factory=list)


def _row(r: sqlite3.Row) -> Series:
    return Series(id=r["id"], root_id=r["root_id"], rel_path=r["rel_path"], fingerprint=r["fingerprint"],
                  n_archives=r["n_archives"], mu_id=r["mu_id"], mu_confirmed=bool(r["mu_confirmed"]),
                  status=r["status"], first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"],
                  kind_hint=r["kind_hint"] if "kind_hint" in r.keys() else None,
                  missing_since=r["missing_since"] if "missing_since" in r.keys() else None)


class SeriesKindError(ValueError):
    pass


def normalize_kind_hint(kind: Optional[str]) -> Optional[str]:
    """``"volumes"`` / ``"chapters"`` (singular and any case accepted), or None (forget the answer)."""
    if kind is None or (isinstance(kind, str) and not kind.strip()):
        return None
    k = str(getattr(kind, "value", kind)).strip().lower()
    k = {"volume": "volumes", "chapter": "chapters"}.get(k, k)
    if k not in SERIES_KIND_HINTS:
        raise SeriesKindError(f"a series' kind is one of {', '.join(SERIES_KIND_HINTS)}, not {kind!r}")
    return k


def link_key(root_dir: Path, rel_path: str) -> str:
    """The links-cache key of a series: the absolute folder as the scanner builds it."""
    return str(Path(root_dir).joinpath(*rel_path.split("/")))


class SeriesMixin:
    def list_series(self, root_id: Optional[int] = None) -> List[Series]:
        with self.connect() as con:
            if root_id is None:
                rows = con.execute("SELECT * FROM series ORDER BY root_id, rel_path").fetchall()
            else:
                rows = con.execute("SELECT * FROM series WHERE root_id = ? ORDER BY rel_path",
                                   (root_id,)).fetchall()
        return [_row(r) for r in rows]

    def get_series(self, root_id: int, rel_path: str) -> Optional[Series]:
        with self.connect() as con:
            r = con.execute("SELECT * FROM series WHERE root_id = ? AND rel_path = ?",
                            (root_id, rel_path)).fetchone()
        return _row(r) if r else None

    def record_scan(self, root_id: int, root_dir: Path, seen: Iterable[SeriesSeen]) -> ScanRecord:
        """Bring the root's series rows in line with a scan of *root_dir* (the folder the scanner
        walked, resolved).

        - A known folder: fingerprint / count / last seen refreshed.
        - New folders: new rows. Vanished folders: kept, marked ``missing`` (``missing_since`` set).
          Whether a vanished series moved is decided by its archives, across all roots
          (:mod:`mangalist.identity`), after this.
        - No re-link here any more: the folder fingerprint (the multiset of archive sizes) is still stored,
          but sizes alone never pair (schema 4 replaced the fingerprint re-link by the archive identity).
        - Every present row takes its MangaUpdates identity (id + confirmed) from the links cache.
        """
        seen = {s.rel_path: s for s in seen}
        now = utcnow()
        rec = ScanRecord()
        with self.connect() as con:
            rows = {r["rel_path"]: _row(r) for r in
                    con.execute("SELECT * FROM series WHERE root_id = ?", (root_id,))}
            gone = [s for rel, s in rows.items() if rel not in seen]

            for s in seen.values():
                if s.rel_path in rows:
                    con.execute("UPDATE series SET fingerprint=?, n_archives=?, status='present', last_seen_at=?,"
                                " missing_since=NULL WHERE id=?", (s.fingerprint, s.n_archives, now, rows[s.rel_path].id))
                else:
                    con.execute("INSERT INTO series (root_id, rel_path, fingerprint, n_archives, status,"
                                " first_seen_at, last_seen_at) VALUES (?,?,?,?, 'present', ?, ?)",
                                (root_id, s.rel_path, s.fingerprint, s.n_archives, now, now))
                    rec.added.append(s.rel_path)
            for s in gone:
                con.execute("UPDATE series SET status='missing', missing_since=COALESCE(missing_since, ?)"
                            " WHERE id=?", (now, s.id))
                rec.missing.append(s.rel_path)

            links = {r["folder"]: (r["mu_id"], r["mu_confirmed"]) for r in
                     con.execute("SELECT folder, mu_id, mu_confirmed FROM links_cache")}
            for rel in seen:
                mu_id, conf = links.get(link_key(root_dir, rel), (None, 0))
                con.execute("UPDATE series SET mu_id=?, mu_confirmed=? WHERE root_id=? AND rel_path=?",
                            (mu_id, 1 if conf else 0, root_id, rel))
        return rec

    # --- the "volumes or chapters?" answer (Design Decisions C12) -----------------------------------

    def set_series_kind(self, root_id: int, rel_path: str, kind: Optional[str]) -> bool:
        """Store the owner's answer for one series (``"volumes"`` / ``"chapters"``; None forgets it).
        It stays with the series row, so a renamed folder keeps it. False when there is no such row."""
        k = normalize_kind_hint(kind)
        with self.connect() as con:
            cur = con.execute("UPDATE series SET kind_hint=? WHERE root_id=? AND rel_path=?", (k, root_id, rel_path))
        return cur.rowcount > 0

    def set_series_kind_for_folder(self, folder, kind: Optional[str]) -> bool:
        """:meth:`set_series_kind` for an absolute series *folder* (as the scanner gives it)."""
        located = self._locate(folder)
        if located is None:
            normalize_kind_hint(kind)   # still refuse a bad value
            return False
        return self.set_series_kind(located[0], located[1], kind)

    def series_kind(self, root_id: int, rel_path: str) -> Optional[str]:
        s = self.get_series(root_id, rel_path)
        return s.kind_hint if s else None

    def series_kind_hints(self, root_id: int) -> Dict[str, str]:
        """``{root-relative path: "volumes" | "chapters"}`` of the root's series that have an answer."""
        with self.connect() as con:
            rows = con.execute("SELECT rel_path, kind_hint FROM series WHERE root_id = ? AND kind_hint IS NOT NULL",
                               (root_id,)).fetchall()
        return {r["rel_path"]: r["kind_hint"] for r in rows}

    def sync_identity(self, folder) -> None:
        """Copy the links-cache identity of the absolute *folder* onto its series row (if any)."""
        located = self._locate(folder)
        if located is None:
            return
        root_id, rel = located
        with self.connect() as con:
            r = con.execute("SELECT mu_id, mu_confirmed FROM links_cache WHERE folder = ?",
                            (str(folder),)).fetchone()
            con.execute("UPDATE series SET mu_id=?, mu_confirmed=? WHERE root_id=? AND rel_path=?",
                        (r["mu_id"] if r else None, (1 if r["mu_confirmed"] else 0) if r else 0, root_id, rel))

    def relink_folder(self, old_folder, new_folder) -> bool:
        """A series folder moved (by the journal): re-point its row (also into another root) and its
        links-cache entry. The archive rows follow through :meth:`follow_journal_move`."""
        old_loc, new_loc = self._locate(old_folder), self._locate(new_folder)
        moved = False
        with self.connect() as con:
            if old_loc is not None and new_loc is not None:
                exists = con.execute("SELECT 1 FROM series WHERE root_id=? AND rel_path=?", new_loc).fetchone()
                if not exists:
                    cur = con.execute("UPDATE series SET root_id=?, rel_path=? WHERE root_id=? AND rel_path=?",
                                      (new_loc[0], new_loc[1], old_loc[0], old_loc[1]))
                    moved = cur.rowcount > 0
            moved = _rekey_link(con, str(old_folder), str(new_folder)) or moved
        return moved

    def _locate(self, folder) -> Optional[Tuple[int, str]]:
        """(root id, root-relative path) of an absolute folder inside a root, else None. Tries the path as
        given and the root resolved (the scanner keys folders under the resolved root)."""
        p = os.fspath(folder)
        for root in self.list_roots():
            for base in {root.path, str(Path(root.path).resolve())}:
                nb, np_ = os.path.normcase(base), os.path.normcase(p)
                if np_.startswith(nb.rstrip(os.sep) + os.sep):
                    return root.id, rel_posix(Path(p), Path(base))
        return None


def _rekey_link(con: sqlite3.Connection, old_key: str, new_key: str) -> bool:
    if old_key == new_key:
        return False
    if con.execute("SELECT 1 FROM links_cache WHERE folder = ?", (new_key,)).fetchone():
        return False  # the new path has its own link: never overwrite it
    cur = con.execute("UPDATE links_cache SET folder = ? WHERE folder = ?", (new_key, old_key))
    return cur.rowcount > 0


def seen_from_entries(root_dir: Path, entries: Iterable) -> List[SeriesSeen]:
    """:class:`SeriesSeen` rows for scanner entries (``MangaEntry``) under *root_dir*."""
    out: Dict[str, SeriesSeen] = {}
    for e in entries:
        rel = rel_posix(Path(e.folder), Path(root_dir))
        if not rel or rel.startswith(".."):
            continue
        sizes = [f.size for f in e.files]
        out[rel] = SeriesSeen(rel, folder_fingerprint(sizes), len(sizes))
    return list(out.values())
