"""Archive rows in step with a scan, and archive move detection (port of MangaPixer's
``LibraryScanCoordinator.DetectMovesAsync`` + ``CopiedElsewhereAsync`` and ``MovePairing``).

:func:`record_archives` (called by :func:`mangalist.scanner.record_library_scan` after the series rows):

1. Every archive a scan saw is upserted by (root, root-relative path); a known path with another size or mtime
   is a change in place: its signature is cleared (re-signed by the backfill). Rows of a scanned root that were
   not seen become ``missing`` (``missing_since``). Roots that failed to scan are left alone.
2. **Moves** - the pool: missing rows (any root) inside the move window (setting ``move_window_days``, default
   30) with a usable signature. A NEW path whose size equals a pool row's is hashed (stamp re-checked after
   hashing, so a file still being written never matches); exactly one pool row and exactly one new path with
   the same signature = a move: the row is re-pointed (same id) and the move is recorded in ``archive_moves``.
   Several rows or paths sharing a signature are ambiguous, and so is a pool row missing since an earlier scan
   when a live archive with its signature appeared after it was last seen (a copy elsewhere). Size alone never
   pairs. Unpaired new paths become new rows (with the signature already computed, if any).
3. **After the fact** (:func:`pair_after_the_fact`, ``MovePairingService``): a missing row and a live row that
   appeared only after the missing one was last seen, the only two with that signature, are one archive (the
   destination was scanned before the source vanished, e.g. a root that was offline): the old row takes the new
   path and the new row is folded into it. A live same-size row without a signature is signed on the spot.
4. **Carry-over** (:mod:`.carry`) for every missing series archives moved out of.

Cost: one 128 KiB read per new archive whose size equals a pool row's; nothing is read when the pool is empty.
No Qt here.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..store.archives import abs_path, archive_of, next_scan_revision
from ..store.db import utcnow
from ..store.exclusions import rel_posix
from .carry import CarryResult, carry_moved_series
from .signature import bytes_read_for, is_usable, signature_if_unchanged

_log = logging.getLogger(__name__)

Signer = Callable[[Path, int, int], Optional[str]]


@dataclass
class ArchiveMove:
    archive_id: int
    from_root_id: int
    from_path: str
    to_root_id: int
    to_path: str
    from_series_id: Optional[int]
    to_series_id: Optional[int]
    how: str


@dataclass
class IdentityReport:
    """What :func:`record_archives` did."""

    scan: int = 0
    archives_seen: int = 0
    archives_new: int = 0
    archives_missing: int = 0           # newly missing in this scan
    archives_changed: int = 0           # changed in place (signature cleared)
    moves: List[ArchiveMove] = field(default_factory=list)
    ambiguous: int = 0                  # signatures shared by several missing rows / new paths, or copied elsewhere
    hashed: int = 0
    bytes_read: int = 0
    carries: List[CarryResult] = field(default_factory=list)

    @property
    def renamed(self) -> List[Tuple[Path, Path]]:
        """``(old folder, new folder)`` of every series that carried something to another folder."""
        return [(Path(c.old_folder), Path(c.new_folder)) for c in self.carries if c.carried]


@dataclass
class PairingResult:
    moves: List[ArchiveMove] = field(default_factory=list)
    ambiguous: int = 0
    waiting: int = 0                    # a same-size live archive could not be signed yet
    hashed: int = 0
    bytes_read: int = 0
    carries: List[CarryResult] = field(default_factory=list)


@dataclass
class _Obs:
    root_id: int
    rel_path: str
    series_id: Optional[int]
    size: int
    mtime_ns: int
    path: Path
    signature: Optional[str] = None


def _default_signer(path: Path, size: int, mtime_ns: int) -> Optional[str]:
    return signature_if_unchanged(path, size, mtime_ns)


def window_cutoff(days: int, now: Optional[datetime] = None) -> str:
    """The oldest ``missing_since`` still inside the move window (same ISO format as the stored times)."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=int(days))).replace(microsecond=0).isoformat()


def _observations(con, root_id: int, folder: Path, entries: Iterable) -> Dict[str, _Obs]:
    """The archives a scan of one root saw: ``{root-relative path: _Obs}`` (each series' own archives)."""
    series = {r["rel_path"]: r["id"] for r in con.execute("SELECT id, rel_path FROM series WHERE root_id = ?",
                                                          (root_id,))}
    out: Dict[str, _Obs] = {}
    for e in entries:
        sid = series.get(rel_posix(Path(e.folder), folder))
        files = getattr(e, "inventory_files", None)
        for hit in (files if files is not None else e.files):
            rel = rel_posix(Path(hit.path), folder)
            if not rel or rel.startswith(".."):
                continue
            mtime = getattr(hit, "mtime_ns", None)
            size = hit.size
            if mtime is None:
                try:
                    st = Path(hit.path).stat()
                    size, mtime = st.st_size, st.st_mtime_ns
                except OSError:
                    continue
            out[rel] = _Obs(root_id, rel, sid, int(size), int(mtime), Path(hit.path))
    return out


def record_archives(db, scanned: Sequence[Tuple[int, Path, Iterable]], *, signer: Optional[Signer] = None,
                    now: Optional[datetime] = None) -> IdentityReport:
    """Bring the archive rows in line with a library scan, detect moves, carry series (module docstring).

    *scanned*: ``(root id, the folder the scanner walked, its entries)`` for every root that scanned without
    error (the series rows are already recorded)."""
    signer = signer or _default_signer
    rep = IdentityReport()
    stamp = now.replace(microsecond=0).isoformat() if now else utcnow()
    dirs = {rid: Path(folder) for rid, folder, _ in scanned}
    new_obs: List[_Obs] = []
    newly_missing: set = set()
    with db.connect() as con:
        rev = rep.scan = next_scan_revision(con)
        for root_id, folder, entries in scanned:
            observed = _observations(con, root_id, Path(folder), entries)
            rep.archives_seen += len(observed)
            rows = {r["rel_path"]: r for r in con.execute("SELECT * FROM archives WHERE root_id = ?", (root_id,))}
            for rel, ob in observed.items():
                r = rows.get(rel)
                if r is None:
                    new_obs.append(ob)
                    continue
                changed = r["size"] != ob.size or r["mtime_ns"] != ob.mtime_ns
                rep.archives_changed += changed
                con.execute("UPDATE archives SET series_id = ?, size = ?, mtime_ns = ?,"
                            " signature = CASE WHEN ? THEN NULL ELSE signature END, status = 'present',"
                            " last_seen_at = ?, last_seen_scan = ?, missing_since = NULL WHERE id = ?",
                            (ob.series_id, ob.size, ob.mtime_ns, 1 if changed else 0, stamp, rev, r["id"]))
            for rel, r in rows.items():
                if rel not in observed and r["status"] == "present":
                    con.execute("UPDATE archives SET status = 'missing', missing_since = ? WHERE id = ?", (stamp, r["id"]))
                    newly_missing.add(r["id"])
            rep.archives_missing = len(newly_missing)
        cutoff = window_cutoff(db.move_window_days(), now)
        pool = [archive_of(r) for r in con.execute(
            "SELECT * FROM archives WHERE status = 'missing' AND signature IS NOT NULL AND missing_since >= ?", (cutoff,))]
    pool = [p for p in pool if is_usable(p.signature, p.size)]

    # Hash only new paths whose size equals a pool row's (outside any transaction: this reads files).
    pool_by_size: Dict[int, list] = defaultdict(list)
    for p in pool:
        pool_by_size[p.size].append(p)
    by_sig: Dict[str, List[_Obs]] = defaultdict(list)
    for ob in new_obs:
        if ob.size not in pool_by_size:
            continue
        rep.hashed += 1
        rep.bytes_read += bytes_read_for(ob.size)
        ob.signature = signer(ob.path, ob.size, ob.mtime_ns)
        if ob.signature is not None:
            by_sig[ob.signature].append(ob)

    pairs = []
    for sig, cands in by_sig.items():
        rows = [p for p in pool_by_size[cands[0].size] if p.signature == sig]
        if not rows:
            continue
        if len(rows) != 1 or len(cands) != 1:
            _log.debug("%d missing and %d new archives share one signature; not treated as a move", len(rows), len(cands))
            rep.ambiguous += 1
            continue
        pairs.append((cands[0], rows[0]))

    paired_obs = set()
    with db.connect() as con:
        for ob, old in pairs:
            if old.id not in newly_missing and con.execute(
                    "SELECT 1 FROM archives WHERE status = 'present' AND signature = ? AND first_seen_scan > ?",
                    (old.signature, old.last_seen_scan)).fetchone():
                rep.ambiguous += 1           # CopiedElsewhere: a live copy appeared after it was last seen
                continue
            cur = con.execute("UPDATE archives SET root_id = ?, rel_path = ?, series_id = ?, size = ?, mtime_ns = ?,"
                              " status = 'present', missing_since = NULL, last_seen_at = ?, last_seen_scan = ?"
                              " WHERE id = ? AND status = 'missing'",
                              (ob.root_id, ob.rel_path, ob.series_id, ob.size, ob.mtime_ns, stamp, rev, old.id))
            if cur.rowcount != 1:
                continue
            mv = ArchiveMove(old.id, old.root_id, old.rel_path, ob.root_id, ob.rel_path, old.series_id, ob.series_id, "scan")
            _insert_move(con, mv, rev, stamp)
            rep.moves.append(mv)
            paired_obs.add(id(ob))
        for ob in new_obs:
            if id(ob) in paired_obs:
                continue
            con.execute("INSERT INTO archives (root_id, series_id, rel_path, size, mtime_ns, signature, status,"
                        " first_seen_at, last_seen_at, first_seen_scan, last_seen_scan)"
                        " VALUES (?,?,?,?,?,?, 'present', ?, ?, ?, ?)",
                        (ob.root_id, ob.series_id, ob.rel_path, ob.size, ob.mtime_ns, ob.signature, stamp, stamp, rev, rev))
            rep.archives_new += 1
    if rep.moves:
        _log.info("Identity: %d archive(s) recognised at a new path", len(rep.moves))

    pairing = pair_after_the_fact(db, signer=signer, now=now, carry=False)
    rep.moves.extend(pairing.moves)
    rep.ambiguous += pairing.ambiguous
    rep.hashed += pairing.hashed
    rep.bytes_read += pairing.bytes_read
    rep.carries = carry_moved_series(db, (m.from_series_id for m in rep.moves), dirs)
    return rep


def _insert_move(con, mv: ArchiveMove, scan: Optional[int], at: str) -> None:
    con.execute("INSERT INTO archive_moves (archive_id, from_series_id, to_series_id, from_root_id, from_path, to_root_id,"
                " to_path, how, scan, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mv.archive_id, mv.from_series_id, mv.to_series_id, mv.from_root_id, mv.from_path, mv.to_root_id,
                 mv.to_path, mv.how, scan, at))


def pair_after_the_fact(db, *, signer: Optional[Signer] = None, now: Optional[datetime] = None,
                        carry: bool = True) -> PairingResult:
    """Moves recognised after the fact (module docstring, point 3). With *carry*, the series carry-over runs
    for the series archives moved out of."""
    signer = signer or _default_signer
    out = PairingResult()
    cutoff = window_cutoff(db.move_window_days(), now)
    with db.connect() as con:
        olds = [o for o in (archive_of(r) for r in con.execute(
            "SELECT * FROM archives WHERE status = 'missing' AND signature IS NOT NULL AND missing_since >= ?", (cutoff,)))
            if is_usable(o.signature, o.size)]
        if not olds:
            return out
        sigs = sorted({o.signature for o in olds})
        sizes = sorted({o.size for o in olds})
        news = []
        for chunk in _chunks(sigs, 400):
            news += [archive_of(r) for r in con.execute(
                f"SELECT * FROM archives WHERE status = 'present' AND signature IN ({','.join('?' * len(chunk))})", chunk)]
        for chunk in _chunks(sizes, 400):
            news += [archive_of(r) for r in con.execute(
                f"SELECT * FROM archives WHERE status = 'present' AND signature IS NULL AND size IN"
                f" ({','.join('?' * len(chunk))})", chunk)]
        roots = {r["id"]: r["path"] for r in con.execute("SELECT id, path FROM roots")}

    # A same-size live archive that appeared after an old one was last seen, still unsigned: sign it now.
    oldest_seen_by_size: Dict[int, int] = {}
    for o in olds:
        oldest_seen_by_size[o.size] = min(oldest_seen_by_size.get(o.size, o.last_seen_scan), o.last_seen_scan)
    signed = []
    for n in news:
        if n.signature is None and n.first_seen_scan > oldest_seen_by_size.get(n.size, 1 << 62) and n.root_id in roots:
            out.hashed += 1
            out.bytes_read += bytes_read_for(n.size)
            sig = signer(abs_path(roots[n.root_id], n.rel_path), n.size, n.mtime_ns)
            if sig is not None:
                n.signature = sig
                signed.append(n)
    if signed:
        with db.connect() as con:
            for n in signed:
                con.execute("UPDATE archives SET signature = ? WHERE id = ? AND signature IS NULL AND size = ? AND"
                            " mtime_ns = ?", (n.signature, n.id, n.size, n.mtime_ns))

    old_count = Counter(o.signature for o in olds)
    decisions = []
    for old in olds:
        if old_count[old.signature] != 1:
            out.ambiguous += 1
            continue
        if any(n.signature is None and n.size == old.size and n.first_seen_scan > old.last_seen_scan for n in news):
            out.waiting += 1
            continue
        eligible = [n for n in news if n.signature == old.signature and n.size == old.size
                    and n.first_seen_scan > old.last_seen_scan and n.id != old.id]
        if len(eligible) == 1:
            decisions.append((old, eligible[0]))
        elif len(eligible) > 1:
            out.ambiguous += 1
    if decisions:
        stamp = utcnow()
        with db.connect() as con:
            for old, new in decisions:
                still = con.execute("SELECT 1 FROM archives WHERE id = ? AND status = 'missing'", (old.id,)).fetchone()
                cur_new = con.execute("SELECT * FROM archives WHERE id = ? AND status = 'present'", (new.id,)).fetchone()
                if not still or cur_new is None:
                    continue
                cur_new = archive_of(cur_new)
                con.execute("UPDATE archive_moves SET archive_id = ? WHERE archive_id = ?", (old.id, new.id))
                con.execute("DELETE FROM archives WHERE id = ?", (new.id,))
                con.execute("UPDATE archives SET root_id = ?, rel_path = ?, series_id = ?, size = ?, mtime_ns = ?,"
                            " signature = ?, status = 'present', missing_since = NULL, last_seen_at = ?,"
                            " last_seen_scan = ? WHERE id = ?",
                            (cur_new.root_id, cur_new.rel_path, cur_new.series_id, cur_new.size, cur_new.mtime_ns,
                             old.signature, cur_new.last_seen_at, cur_new.last_seen_scan, old.id))
                mv = ArchiveMove(old.id, old.root_id, old.rel_path, cur_new.root_id, cur_new.rel_path, old.series_id,
                                 cur_new.series_id, "pairing")
                _insert_move(con, mv, None, stamp)
                out.moves.append(mv)
        _log.info("Identity: %d archive(s) paired after the fact", len(out.moves))
    if carry and out.moves:
        out.carries = carry_moved_series(db, (m.from_series_id for m in out.moves))
    return out


def _chunks(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield list(seq[i:i + n])


__all__ = ["ArchiveMove", "IdentityReport", "PairingResult", "record_archives", "pair_after_the_fact", "window_cutoff"]
