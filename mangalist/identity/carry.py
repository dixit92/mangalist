"""Series carry-over: a vanished series' data follows its archives to ONE live folder (port of MangaPixer's
``MetadataCarryOverService``, ``MinMovedShare = 0.8``), across all roots.

What a MangaList series carries: the series row itself (its id, first seen, the ledger rows that point at it),
the owner's "volumes or chapters?" answer (``kind_hint``), the own MangaUpdates link (the ``links_cache`` row,
with confirmed flag, scores and the Behind override - re-keyed to the new folder path, the series row's
``mu_id`` / ``mu_confirmed`` following it) and the GUI's examined mark (settings ``examined``).

Rules (:func:`map_target`, MangaPixer's ``MapTarget``):

- the old series O is ``missing``; every archive recognised as moved out of O (the ``archive_moves`` ledger,
  cumulative) now lies in ONE live series T (a split folder has no single target);
- those archives are at least 80% of O's archives (moved + still missing under O);
- T != O.

How (:func:`carry`, MangaPixer's ``MoveRowsAsync``): T's own data is NEVER overwritten.

- T has no data of its own (no link, no kind answer): O's row takes T's place - the same series id, now at
  T's root and path (T's just-created row is folded into it); O's link and examined mark follow.
- Otherwise each kind of data moves only when T has none of that kind; what cannot move stays on O, which
  stays ``missing`` (shown in the Missing series list for a manual decision). An O with nothing left is
  retired (its data now lives on T).

Folder renames within a root and moves between roots are the same case. Manual :func:`reattach` and
:func:`forget` serve the Missing series list. No Qt here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..store.db import utcnow
from ..store.series import link_key

_log = logging.getLogger(__name__)

MIN_MOVED_SHARE = 0.8
EXAMINED_SETTING = "examined"


@dataclass
class CarryResult:
    from_series_id: int
    to_series_id: int
    old_folder: str
    new_folder: str
    how: str                                       # archives | mangapixer | manual
    moved: List[str] = field(default_factory=list)  # row, link, kind, examined
    kept: List[str] = field(default_factory=list)   # what stayed on the old row (T had its own)

    @property
    def merged(self) -> bool:
        return "row" in self.moved

    @property
    def carried(self) -> bool:
        return bool(self.moved)


@dataclass
class MissingSeries:
    id: int
    root_id: int
    root_name: str
    rel_path: str
    folder: str                     # the absolute folder it had (the links-cache key)
    last_seen_at: str
    missing_since: Optional[str]
    n_archives: int
    mu_id: Optional[int]
    has_link: bool
    kind_hint: Optional[str]
    examined: bool

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def has_data(self) -> bool:
        return self.has_link or bool(self.kind_hint) or self.examined


@dataclass
class LiveSeries:
    id: int
    root_id: int
    root_name: str
    rel_path: str
    folder: str
    has_link: bool
    kind_hint: Optional[str]

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def has_own_data(self) -> bool:
        return self.has_link or bool(self.kind_hint)


# --- helpers --------------------------------------------------------------------------------------------------


def root_dirs(con: sqlite3.Connection, overrides: Optional[Dict[int, Path]] = None) -> Dict[int, Path]:
    """Each root's folder as the scanner keys series (resolved), *overrides* (the folders a scan walked) first."""
    out: Dict[int, Path] = {}
    for r in con.execute("SELECT id, path FROM roots"):
        p = Path(r["path"])
        try:
            p = p.resolve()
        except OSError:
            pass
        out[r["id"]] = p
    out.update(overrides or {})
    return out


class _Ctx:
    """One carry pass on one connection: root folders, the examined list (saved once at the end)."""

    def __init__(self, con: sqlite3.Connection, dirs: Optional[Dict[int, Path]] = None, now: Optional[str] = None):
        self.con = con
        self.dirs = root_dirs(con, dirs)
        self.now = now or utcnow()
        self._examined: Optional[List[str]] = None
        self._examined_dirty = False

    def key(self, row: sqlite3.Row) -> str:
        return link_key(self.dirs.get(row["root_id"], Path(".")), row["rel_path"])

    @property
    def examined(self) -> List[str]:
        if self._examined is None:
            r = self.con.execute("SELECT value FROM settings WHERE key = ?", (EXAMINED_SETTING,)).fetchone()
            try:
                value = json.loads(r["value"]) if r is not None else []
            except (TypeError, ValueError):
                value = []
            self._examined = [str(p) for p in value] if isinstance(value, list) else []
        return self._examined

    def move_examined(self, old: str, new: str) -> bool:
        ex = self.examined
        if old not in ex:
            return False
        self._examined = [p for p in ex if p != old] + ([] if new in ex else [new])
        self._examined_dirty = True
        return True

    def drop_examined(self, key: str) -> None:
        if key in self.examined:
            self._examined = [p for p in self.examined if p != key]
            self._examined_dirty = True

    def flush(self) -> None:
        if self._examined_dirty:
            self.con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                             " value = excluded.value", (EXAMINED_SETTING, json.dumps(self._examined, ensure_ascii=False)))
            self._examined_dirty = False


def _series(con: sqlite3.Connection, series_id: int) -> Optional[sqlite3.Row]:
    return con.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()


def _has_link(con: sqlite3.Connection, key: str) -> bool:
    return con.execute("SELECT 1 FROM links_cache WHERE folder = ?", (key,)).fetchone() is not None


def _sync_mu(con: sqlite3.Connection, series_id: int, key: str) -> None:
    r = con.execute("SELECT mu_id, mu_confirmed FROM links_cache WHERE folder = ?", (key,)).fetchone()
    con.execute("UPDATE series SET mu_id = ?, mu_confirmed = ? WHERE id = ?",
                (r["mu_id"] if r else None, (1 if r["mu_confirmed"] else 0) if r else 0, series_id))


def _rekey(con: sqlite3.Connection, old_key: str, new_key: str) -> bool:
    if old_key == new_key or _has_link(con, new_key):
        return False
    return con.execute("UPDATE links_cache SET folder = ? WHERE folder = ?", (new_key, old_key)).rowcount > 0


# --- the rule ---------------------------------------------------------------------------------------------------


def map_target(con: sqlite3.Connection, old_series_id: int, min_share: float = MIN_MOVED_SHARE) -> Optional[int]:
    """Where the missing series *old_series_id* went: the one live series that holds every archive recognised
    as moved out of it, when those are >= *min_share* of its archives; else None."""
    o = _series(con, old_series_id)
    if o is None or o["status"] != "missing":
        return None
    moved = con.execute(
        "SELECT DISTINCT a.id, a.series_id FROM archive_moves m JOIN archives a ON a.id = m.archive_id"
        " WHERE m.from_series_id = ? AND a.status = 'present'", (old_series_id,)).fetchall()
    if not moved:
        return None
    targets = {r["series_id"] for r in moved}
    if len(targets) != 1 or None in targets:
        return None                                     # split folder (or a file outside any series)
    target = targets.pop()
    if target == old_series_id:
        return None
    t = _series(con, target)
    if t is None or t["status"] != "present":
        return None
    remaining = con.execute("SELECT COUNT(*) FROM archives WHERE series_id = ? AND status = 'missing'",
                            (old_series_id,)).fetchone()[0]
    n = len(moved)
    return target if n >= min_share * (n + remaining) else None


def carry(con: sqlite3.Connection, ctx: _Ctx, old_id: int, new_id: int, how: str) -> Optional[CarryResult]:
    """Move the data of the missing series *old_id* to the live series *new_id*, never over the target's own data
    (see the module docstring). Records the outcome in ``series_carries``."""
    o, t = _series(con, old_id), _series(con, new_id)
    if o is None or t is None or old_id == new_id or o["status"] != "missing" or t["status"] != "present":
        return None
    key_o, key_t = ctx.key(o), ctx.key(t)
    res = CarryResult(old_id, new_id, key_o, key_t, how)
    o_link, t_link = _has_link(con, key_o), _has_link(con, key_t)
    o_kind, t_kind = o["kind_hint"], t["kind_hint"]
    if not t_link and not t_kind:
        # T has nothing of its own: O's row takes T's place (same id), T's fresh row is folded into it.
        con.execute("UPDATE archives SET series_id = ? WHERE series_id = ?", (old_id, new_id))
        con.execute("UPDATE archive_moves SET to_series_id = ? WHERE to_series_id = ?", (old_id, new_id))
        con.execute("UPDATE ledger SET series_id = ? WHERE series_id = ?", (old_id, new_id))
        con.execute("DELETE FROM units WHERE series_id = ?", (old_id,))
        con.execute("UPDATE units SET series_id = ? WHERE series_id = ?", (old_id, new_id))
        con.execute("DELETE FROM series WHERE id = ?", (new_id,))
        con.execute("UPDATE series SET root_id = ?, rel_path = ?, fingerprint = ?, n_archives = ?, status = 'present',"
                    " last_seen_at = ?, missing_since = NULL WHERE id = ?",
                    (t["root_id"], t["rel_path"], t["fingerprint"], t["n_archives"], t["last_seen_at"], old_id))
        res.moved.append("row")
        res.to_series_id = old_id
        if o_link and _rekey(con, key_o, key_t):
            res.moved.append("link")
        if o_kind:
            res.moved.append("kind")
        if ctx.move_examined(key_o, key_t):
            res.moved.append("examined")
        _sync_mu(con, old_id, key_t)
    else:
        if o_link:
            if t_link:
                res.kept.append("link")
            elif _rekey(con, key_o, key_t):
                res.moved.append("link")
        if o_kind:
            if t_kind:
                res.kept.append("kind")
            else:
                con.execute("UPDATE series SET kind_hint = ? WHERE id = ?", (o_kind, new_id))
                con.execute("UPDATE series SET kind_hint = NULL WHERE id = ?", (old_id,))
                res.moved.append("kind")
        if ctx.move_examined(key_o, key_t):
            res.moved.append("examined")
        _sync_mu(con, new_id, key_t)
        if res.kept:
            _sync_mu(con, old_id, key_o)
        else:
            # Nothing left on O: retire it (its data lives on T now; its unmatched archive rows stay, unattached).
            con.execute("UPDATE ledger SET series_id = ? WHERE series_id = ?", (new_id, old_id))
            con.execute("DELETE FROM series WHERE id = ?", (old_id,))
    con.execute("INSERT INTO series_carries (from_series_id, to_series_id, from_root_id, from_path, to_root_id, to_path,"
                " how, moved, kept, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (old_id, new_id, o["root_id"], o["rel_path"], t["root_id"], t["rel_path"], how,
                 json.dumps(res.moved), json.dumps(res.kept), ctx.now))
    _log.info("Series carry-over (%s): series %d -> %d: moved %s, kept %s", how, old_id, new_id,
              ",".join(res.moved) or "-", ",".join(res.kept) or "-")
    return res


def carry_moved_series(db, from_series_ids: Iterable[Optional[int]], dirs: Optional[Dict[int, Path]] = None,
                       how: str = "archives") -> List[CarryResult]:
    """Run the carry-over rule for every missing series archives were just recognised as moved out of."""
    ids = sorted({int(i) for i in from_series_ids if i is not None})
    out: List[CarryResult] = []
    if not ids:
        return out
    with db.connect() as con:
        ctx = _Ctx(con, dirs)
        for old_id in ids:
            target = map_target(con, old_id)
            if target is None:
                continue
            res = carry(con, ctx, old_id, target, how)
            if res is not None:
                out.append(res)
        ctx.flush()
    return out


# --- the Missing series list -------------------------------------------------------------------------------------


def missing_series(db) -> List[MissingSeries]:
    """Every missing series row (all roots), oldest first."""
    with db.connect() as con:
        ctx = _Ctx(con)
        names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM roots")}
        examined = set(ctx.examined)
        out = []
        for r in con.execute("SELECT * FROM series WHERE status = 'missing' ORDER BY last_seen_at, id"):
            key = ctx.key(r)
            out.append(MissingSeries(
                id=r["id"], root_id=r["root_id"], root_name=names.get(r["root_id"], ""), rel_path=r["rel_path"],
                folder=key, last_seen_at=r["last_seen_at"], missing_since=r["missing_since"],
                n_archives=r["n_archives"], mu_id=r["mu_id"], has_link=_has_link(con, key), kind_hint=r["kind_hint"],
                examined=key in examined))
    return out


def missing_count(db) -> int:
    with db.connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM series WHERE status = 'missing'").fetchone()[0])


def live_series(db, without_own_data: bool = False) -> List[LiveSeries]:
    """Present series rows (re-attach targets); *without_own_data*: only those with no link and no kind answer."""
    with db.connect() as con:
        ctx = _Ctx(con)
        names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM roots")}
        out = []
        for r in con.execute("SELECT * FROM series WHERE status = 'present' ORDER BY root_id, rel_path"):
            key = ctx.key(r)
            s = LiveSeries(id=r["id"], root_id=r["root_id"], root_name=names.get(r["root_id"], ""),
                           rel_path=r["rel_path"], folder=key, has_link=_has_link(con, key), kind_hint=r["kind_hint"])
            if not (without_own_data and s.has_own_data):
                out.append(s)
    return out


class ReattachError(ValueError):
    pass


def reattach(db, missing_id: int, live_id: int) -> CarryResult:
    """The owner re-attaches a missing series to a live folder (never over that folder's own data)."""
    with db.connect() as con:
        o, t = _series(con, missing_id), _series(con, live_id)
        if o is None or o["status"] != "missing":
            raise ReattachError("only a missing series can be re-attached")
        if t is None or t["status"] != "present":
            raise ReattachError("the target must be a live series folder")
        ctx = _Ctx(con)
        res = carry(con, ctx, missing_id, live_id, "manual")
        ctx.flush()
    if res is None:
        raise ReattachError("nothing could be re-attached")
    return res


def forget(db, missing_id: int) -> bool:
    """Forget a missing series (the owner confirmed): its row, its own link (links cache) and its examined mark.
    Its archive rows stay (unattached), so a later scan can still recognise the files. False when the row is
    not a missing series."""
    with db.connect() as con:
        o = _series(con, missing_id)
        if o is None or o["status"] != "missing":
            return False
        ctx = _Ctx(con)
        key = ctx.key(o)
        con.execute("DELETE FROM links_cache WHERE folder = ?", (key,))
        ctx.drop_examined(key)
        con.execute("UPDATE archives SET series_id = NULL WHERE series_id = ?", (missing_id,))
        con.execute("DELETE FROM series WHERE id = ?", (missing_id,))
        ctx.flush()
    _log.info("Missing series %d forgotten", missing_id)
    return True


__all__ = ["MIN_MOVED_SHARE", "CarryResult", "MissingSeries", "LiveSeries", "ReattachError", "map_target", "carry",
           "carry_moved_series", "missing_series", "missing_count", "live_series", "reattach", "forget",
           "root_dirs"]
