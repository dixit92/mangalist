"""MangaPixer's ``carriedFrom`` as an extra, authoritative identity layer (optional; MangaList works without it).

When MangaPixer's own carry-over moved a folder's link to a new folder (a rename or a move recognised by its
signatures, >= 80% of the archives), its export lists the new folder item with ``carriedFrom`` = the old node.
The MangaPixer sync re-keys the old item row to the new node, so the old node's trail is kept here first
(:func:`record_carries`, table ``mangapixer_carries``).

:func:`apply_pending` (after each sync and each recorded scan): for each pair not yet applied, the old trail is
mapped to a MangaList series through the root mappings (``trail = prefix + root-relative path``) and the new
node's trail to a live MangaList folder. When the old series is MISSING and the new folder is a live series, the
old series is carried to it (:func:`mangalist.identity.carry.carry`, no 80% check - MangaPixer already decided;
still never over the target's own data). A pair whose old folder is still present waits (MangaList has not
seen the rename yet); one whose old folder MangaList has no row for is settled as ``nothing``; pairs older than
the move window expire. Missing rows are kept, so a later sync or scan can still re-attach. No Qt here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..store.db import utcnow
from ..store.mangapixer import fold_key, nfc
from .carry import CarryResult, _Ctx, carry
from .moves import window_cutoff

_log = logging.getLogger(__name__)


def record_carries(store, library_id: str, pairs: Iterable[Tuple[str, str, Sequence[str], Optional[Sequence[str]]]]) -> int:
    """Keep ``(old nodeId, new nodeId, old trail, new trail)`` pairs a sync saw (first sighting wins)."""
    now = utcnow()
    n = 0
    with store.connect() as con:
        for old, new, old_trail, new_trail in pairs:
            cur = con.execute(
                "INSERT INTO mangapixer_carries (library_id, old_node_id, new_node_id, old_trail, new_trail, seen_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(library_id, old_node_id) DO UPDATE SET"
                " new_node_id = excluded.new_node_id, new_trail = excluded.new_trail"
                " WHERE mangapixer_carries.outcome IS NULL",
                (library_id, str(old), str(new), json.dumps([str(p) for p in old_trail], ensure_ascii=False),
                 json.dumps([str(p) for p in new_trail], ensure_ascii=False) if new_trail is not None else None, now))
            n += cur.rowcount
    return n


def _rel_for(trail: Sequence[str], prefix: Sequence[str]) -> Optional[str]:
    t = [nfc(p) for p in trail]
    p = [nfc(x) for x in prefix]
    if len(t) <= len(p):
        return None
    if t[:len(p)] == p or fold_key(t[:len(p)]) == fold_key(p):
        return "/".join(t[len(p):])
    return None


def _find_series(con, library_id: str, trail: Sequence[str], mappings: Dict[int, Any]) -> Optional[Any]:
    """The MangaList series row at *trail* of *library_id* (longest mapping prefix; exact, then casefold)."""
    best = None
    for m in mappings.values():
        if m.library_id != library_id:
            continue
        rel = _rel_for(trail, m.prefix)
        if rel is None:
            continue
        if best is None or len(m.prefix) > len(best[0].prefix):
            best = (m, rel)
    if best is None:
        return None
    m, rel = best
    r = con.execute("SELECT * FROM series WHERE root_id = ? AND rel_path = ?", (m.root_id, rel)).fetchone()
    if r is not None:
        return r
    folded = fold_key(rel.split("/"))
    for r in con.execute("SELECT * FROM series WHERE root_id = ?", (m.root_id,)):
        if fold_key(r["rel_path"].split("/")) == folded:
            return r
    return None


def apply_pending(store, now: Optional[datetime] = None) -> List[CarryResult]:
    """Carry missing MangaList series along MangaPixer's ``carriedFrom`` pairs (module docstring)."""
    from ..store.mangapixer import MangaPixerCache

    cache = MangaPixerCache(store)
    out: List[CarryResult] = []
    stamp = utcnow()
    cutoff = window_cutoff(store.move_window_days(), now)
    with store.connect() as con:
        pending = con.execute("SELECT * FROM mangapixer_carries WHERE outcome IS NULL ORDER BY seen_at").fetchall()
        if not pending:
            return out
    mappings = cache.mappings()
    with store.connect() as con:
        ctx = _Ctx(con)
        for p in pending:
            def settle(outcome: str) -> None:
                con.execute("UPDATE mangapixer_carries SET outcome = ?, applied_at = ? WHERE library_id = ? AND"
                            " old_node_id = ?", (outcome, stamp, p["library_id"], p["old_node_id"]))

            if p["seen_at"] < cutoff:
                settle("expired")
                continue
            old_trail = json.loads(p["old_trail"])
            item = con.execute("SELECT trail FROM mangapixer_items WHERE library_id = ? AND node_id = ?",
                               (p["library_id"], p["new_node_id"])).fetchone()
            new_trail = json.loads(item["trail"]) if item is not None else (
                json.loads(p["new_trail"]) if p["new_trail"] else None)
            old = _find_series(con, p["library_id"], old_trail, mappings)
            if old is None:
                settle("nothing")            # MangaList has no series there (or it was settled another way)
                continue
            if old["status"] != "missing":
                continue                     # MangaList has not seen the rename yet: wait for its scan
            new = _find_series(con, p["library_id"], new_trail, mappings) if new_trail else None
            if new is None or new["status"] != "present" or new["id"] == old["id"]:
                continue                     # the new folder is not a live MangaList series (yet)
            res = carry(con, ctx, old["id"], new["id"], "mangapixer")
            if res is None:
                continue
            settle("carried" if res.carried else "kept")
            out.append(res)
        ctx.flush()
    if out:
        _log.info("MangaPixer carriedFrom: %d series carried", sum(1 for r in out if r.carried))
    return out


__all__ = ["record_carries", "apply_pending"]
